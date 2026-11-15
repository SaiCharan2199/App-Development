"""Tests for pipeline/h1b.py: cleaning, wages, reading Excel, summarising and matching."""

from pathlib import Path

import pytest

from pipeline import h1b

HEADERS = ["CASE_NUMBER", "CASE_STATUS", "VISA_CLASS", "JOB_TITLE", "EMPLOYER_NAME",
           "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY", "TOTAL_WORKER_POSITIONS"]

SAMPLE_ROWS = [
    ["I-1", "Certified", "H-1B", "Senior Data Engineer", "Stripe, Inc.", "CA", 180000, "Year", 1],
    ["I-2", "Certified", "H-1B", "Data Engineer", "STRIPE INC", "WA", "$160,000.00", "Year", 1],
    ["I-3", "Certified - Withdrawn", "H-1B", "Data Engineer II", "Stripe, Inc.", "CA", 80, "Hour", 2],
    ["I-4", "Denied", "H-1B", "Data Engineer", "Stripe, Inc.", "CA", 150000, "Year", 1],        # denied
    ["I-5", "Certified", "E-3 Australian", "Data Engineer", "Stripe, Inc.", "CA", 150000, "Year", 1],  # not H-1B
    ["I-6", "Certified", "H-1B", "Software Engineer", "Maplebear Inc.", "CA", 170000, "Year", 1],
    ["I-7", "Certified", "H-1B", "Software Engineer", "Some Other Corp", "TX", 9, "Hour", 1],    # implausible wage
]


def as_dicts(rows):
    columns, _ = h1b.map_columns(HEADERS)
    return [{field: row[i] for field, i in columns.items()} for row in rows]


# ---------------- Pure helpers ----------------

@pytest.mark.parametrize("raw, expected", [
    ("Stripe, Inc.", "STRIPE"),
    ("STRIPE INC", "STRIPE"),
    ("Anthropic, PBC", "ANTHROPIC"),
    ("Palantir Technologies Inc.", "PALANTIR TECHNOLOGIES"),
    ("The Walt Disney Company", "WALT DISNEY"),
    ("Johnson & Johnson", "JOHNSON AND JOHNSON"),
    ("Social Finance, LLC", "SOCIAL FINANCE"),
    # Found in real data:
    ("Formagrid Inc. d/b/a Airtable", "FORMAGRID"),
    ("Formagrid Inc DBA Airtable", "FORMAGRID"),
    ("SoFi Bank, N.A.", "SOFI BANK"),
    ("Zoox, Inc.", "ZOOX"),
    ("Inc", ""),
    (None, ""),
])
def test_normalize_employer(raw, expected):
    assert h1b.normalize_employer(raw) == expected


@pytest.mark.parametrize("amount, unit, expected", [
    (150000, "Year", 150000),
    ("$160,000.00", "Year", 160000),
    (80, "Hour", 166400),
    (10000, "Month", 120000),
    (3000, "Week", 156000),
    (6000, "Bi-Weekly", 156000),
    (9, "Hour", None),            # $18,720/yr: below a plausible salary
    (None, "Year", None),
    ("n/a", "Year", None),
    (150000, "Decade", None),
])
def test_annual_wage(amount, unit, expected):
    assert h1b.annual_wage(amount, unit) == expected


def test_only_certified_h1b_rows_count():
    rows = as_dicts(SAMPLE_ROWS)
    assert [h1b.is_certified_h1b(r) for r in rows] == [True, True, True, False, False, True, True]


@pytest.mark.parametrize("name, expected", [
    ("LCA_Disclosure_Data_FY2025_Q4.xlsx", 2025),
    ("LCA_Disclosure_Data_FY2026_Q3.xlsx", 2026),
    ("h1b_data.xlsx", None),
])
def test_fiscal_year_from_filename(name, expected):
    assert h1b.fiscal_year_from_filename(Path(name)) == expected


def test_map_columns_finds_fields_and_reports_missing():
    columns, missing = h1b.map_columns(["case_status", "Visa Class", "JOB_TITLE"])
    assert columns == {"case_status": 0, "visa_class": 1, "job_title": 2}
    assert missing == ["employer_name", "wage_from", "wage_unit"]


# ---------------- Summarising ----------------

def test_summarize_groups_by_employer_and_role():
    summaries, counts = h1b.summarize(as_dicts(SAMPLE_ROWS))
    by_key = {(s["employer_key"], s["role_family"]): s for s in summaries}

    stripe = by_key[("STRIPE", "data_engineer")]
    assert stripe["filings"] == 3                    # denied and E-3 rows excluded
    assert stripe["positions"] == 4
    assert stripe["wage_median"] == 166400           # median of 160k, 166.4k (80/hr), 180k
    assert stripe["employer_name"] == "Stripe, Inc." # most common spelling
    assert stripe["top_states"] == "CA, WA"

    other = by_key[("SOME OTHER", "software_engineer")]
    assert other["wage_median"] is None              # implausible wage ignored, filing still counted
    assert other["filings"] == 1

    assert counts["rows"] == 7 and counts["certified_h1b"] == 5 and counts["employers"] == 3


def test_read_rows_from_a_real_excel_file(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "LCA_Disclosure_Data_FY2025_Q4.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    for row in SAMPLE_ROWS:
        sheet.append(row)
    workbook.save(path)

    rows = list(h1b.read_rows(path))
    assert len(rows) == 7
    assert rows[0]["employer_name"] == "Stripe, Inc."
    assert rows[0]["wage_unit"] == "Year"


def test_read_rows_fails_clearly_when_columns_are_missing(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "bad.xlsx"
    workbook = Workbook()
    workbook.active.append(["SOMETHING", "ELSE"])
    workbook.save(path)
    with pytest.raises(ValueError, match="Columns not found"):
        list(h1b.read_rows(path))


# ---------------- Database (rolled back) ----------------

@pytest.fixture
def db_rolled_back():
    """A connection inside a transaction that is ALWAYS rolled back.

    save() and match_companies() open their own transactions, which would COMMIT if
    called on an idle connection. Inside this outer transaction they become
    savepoints, and force_rollback undoes everything, even when the test passes.
    """
    psycopg = pytest.importorskip("psycopg")
    from pipeline.db import get_connection
    try:
        conn = get_connection()
    except (psycopg.OperationalError, KeyError) as exc:
        pytest.skip(f"database not available: {exc}")
    try:
        with conn.transaction(force_rollback=True):
            yield conn
    finally:
        conn.close()
    with get_connection() as check:            # prove nothing was left behind
        left = check.execute("SELECT count(*) FROM h1b_filings WHERE fiscal_year = 2999").fetchone()[0]
    assert left == 0, "test data must not be left in the database"


@pytest.mark.database
def test_save_match_and_views(db_rolled_back):
    conn = db_rolled_back
    summaries, _ = h1b.summarize(as_dicts(SAMPLE_ROWS))
    assert h1b.save(conn, summaries, 2999, "test.xlsx") == len(summaries)

    filings = h1b.match_companies(conn)
    assert filings.get("Stripe", 0) >= 3                  # matched by cleaned name
    assert filings.get("Instacart", 0) >= 1               # matched by alias MAPLEBEAR

    row = conn.execute(
        "SELECT s.filings, s.wage_typical FROM company_h1b_summary s JOIN companies c ON c.id = s.company_id "
        "WHERE c.name = 'Stripe' AND s.fiscal_year = 2999 AND s.role_family = 'data_engineer'"
    ).fetchone()
    assert row == (3, 166400)

    # A Stripe data engineer job should see Stripe's FY 2999 data engineer filings.
    job = conn.execute(
        "SELECT h.h1b_fiscal_year, h.h1b_role_filings FROM job_h1b_history h JOIN jobs j ON j.id = h.job_id "
        "JOIN companies c ON c.id = j.company_id WHERE c.name = 'Stripe' AND j.role_family = 'data_engineer' LIMIT 1"
    ).fetchone()
    if job:                                               # only if such a job is currently stored
        assert job == (2999, 3)
