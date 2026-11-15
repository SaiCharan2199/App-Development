"""Load H-1B history from the US Department of Labor's LCA disclosure files.

The DOL website blocks automated downloads, so download the file in a browser from
https://www.dol.gov/agencies/eta/foreign-labor/performance (LCA Programs ->
Disclosure Data) and save it in data/h1b/. Then:

    python -m pipeline.h1b data/h1b/LCA_Disclosure_Data_FY2025_Q4.xlsx --check-columns
    python -m pipeline.h1b data/h1b/LCA_Disclosure_Data_FY2025_Q4.xlsx
    python -m pipeline.h1b --report            # how our companies matched

The file has one row per application. We keep certified H-1B applications and store
a summary per fiscal year x employer x role family (filings, wages, top states/titles).
"""

import argparse
import logging
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from pipeline import rules

log = logging.getLogger(__name__)

ALIASES_FILE = Path(__file__).resolve().parent / "config" / "h1b_aliases.yaml"

# Column names as DOL writes them. Matching ignores case, spaces and underscores.
COLUMNS = {
    "case_status": ["CASE_STATUS"],
    "visa_class": ["VISA_CLASS"],
    "job_title": ["JOB_TITLE"],
    "employer_name": ["EMPLOYER_NAME"],
    "wage_from": ["WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY", "WAGE_RATE_OF_PAY_FROM_1"],
    "wage_unit": ["WAGE_UNIT_OF_PAY", "WAGE_UNIT_OF_PAY_1"],
    "worksite_state": ["WORKSITE_STATE", "WORKSITE_STATE_1", "PRIMARY_WORKSITE_STATE"],
    "positions": ["TOTAL_WORKER_POSITIONS", "TOTAL_WORKERS"],
}
REQUIRED = ("case_status", "visa_class", "job_title", "employer_name", "wage_from", "wage_unit")

_WAGE_MULTIPLIER = {"YEAR": 1, "MONTH": 12, "BIWEEKLY": 26, "WEEK": 52, "HOUR": 2080}
# H-1B roles must pay at least the local prevailing wage, so yearly amounts below
# $25k are almost always data-entry errors (e.g. a yearly wage typed as hourly).
_PLAUSIBLE_YEARLY_WAGE = (25_000, 2_000_000)

_LEGAL_SUFFIXES = {
    "INC", "INCORPORATED", "LLC", "LTD", "LIMITED", "CORP", "CORPORATION", "CO", "COMPANY",
    "PBC", "LP", "LLP", "PLC", "PC", "PLLC",
}


# ---------------------------------------------------------------
# Small pure helpers (unit-tested)
# ---------------------------------------------------------------


def normalize_employer(name: str | None) -> str:
    """'Stripe, Inc.' -> 'STRIPE'; 'The Walt Disney Company' -> 'WALT DISNEY'."""
    text = (name or "").upper().replace("&", " AND ")
    text = re.sub(r"[.,'\"()/\\]", " ", text)
    # Drop "doing business as" / "also known as" parts: 'FORMAGRID INC D/B/A AIRTABLE' -> 'FORMAGRID INC'
    text = re.split(r"\b(?:D\s*B\s*A|A\s*K\s*A|F\s*K\s*A)\b", text)[0]
    words = text.split()
    if words[:1] == ["THE"]:
        words = words[1:]
    while words:
        if words[-1] in _LEGAL_SUFFIXES:
            words = words[:-1]
        elif words[-2:] == ["N", "A"]:       # 'SOFI BANK, N.A.' (National Association)
            words = words[:-2]
        else:
            break
    return " ".join(words)


def annual_wage(amount, unit) -> float | None:
    """Offered wage converted to a yearly amount, or None if missing or implausible."""
    if amount in (None, ""):
        return None
    try:
        value = float(str(amount).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    multiplier = _WAGE_MULTIPLIER.get(re.sub(r"[^A-Z]", "", str(unit or "").upper()))
    if not multiplier:
        return None
    yearly = value * multiplier
    low, high = _PLAUSIBLE_YEARLY_WAGE
    return yearly if low <= yearly <= high else None


def is_certified_h1b(row: dict) -> bool:
    status = re.sub(r"[^A-Z]", "", str(row.get("case_status") or "").upper())
    visa = str(row.get("visa_class") or "").strip().upper()
    return visa == "H-1B" and status.startswith("CERTIFIED")    # includes 'Certified - Withdrawn'


def fiscal_year_from_filename(path: Path) -> int | None:
    match = re.search(r"FY[_ -]?(\d{4})", path.name, re.I)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------


def _key(header: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(header or "").upper())


def map_columns(headers: list) -> tuple[dict[str, int], list[str]]:
    """Find each column we need by name. Returns ({field: column_index}, missing_required_fields)."""
    index = {_key(h): i for i, h in enumerate(headers) if h is not None}
    found = {}
    for field, names in COLUMNS.items():
        for name in names:
            if _key(name) in index:
                found[field] = index[_key(name)]
                break
    missing = [f for f in REQUIRED if f not in found]
    return found, missing


def read_rows(path: Path):
    """Yield one dict per application, streaming so memory stays low."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        headers = list(next(rows))
        columns, missing = map_columns(headers)
        if missing:
            raise ValueError(f"Columns not found in {path.name}: {missing}. Headers start: {headers[:15]}")
        for n, values in enumerate(rows, start=1):
            yield {field: values[i] if i < len(values) else None for field, i in columns.items()}
            if n % 100_000 == 0:
                log.info("  read %d rows...", n)
    finally:
        workbook.close()


# ---------------------------------------------------------------
# Summarising
# ---------------------------------------------------------------


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile of an already sorted list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def summarize(rows) -> tuple[list[dict], dict]:
    """Group certified H-1B rows by (employer_key, role_family). Returns (summaries, counts)."""
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "filings": 0, "positions": 0, "wages": [], "states": Counter(), "titles": Counter(), "names": Counter(),
    })
    counts = Counter()
    family_cache: dict[str, str] = {}
    for row in rows:
        counts["rows"] += 1
        if not is_certified_h1b(row):
            continue
        key = normalize_employer(row.get("employer_name"))
        if not key:
            counts["no_employer"] += 1
            continue
        title = str(row.get("job_title") or "").strip()
        family = family_cache.get(title.upper())
        if family is None:
            family = family_cache.setdefault(title.upper(), rules.detect_role_family(title))

        g = groups[(key, family)]
        g["filings"] += 1
        try:
            g["positions"] += max(1, int(row.get("positions") or 1))
        except (TypeError, ValueError):
            g["positions"] += 1
        wage = annual_wage(row.get("wage_from"), row.get("wage_unit"))
        if wage is not None:
            g["wages"].append(wage)
        state = str(row.get("worksite_state") or "").strip().upper()
        if state:
            g["states"][state] += 1
        g["titles"][title.title()] += 1
        g["names"][str(row.get("employer_name")).strip()] += 1
        counts["certified_h1b"] += 1

    summaries = []
    for (key, family), g in groups.items():
        wages = sorted(g["wages"])
        summaries.append({
            "employer_key": key,
            "employer_name": g["names"].most_common(1)[0][0],
            "role_family": family,
            "filings": g["filings"],
            "positions": g["positions"],
            "wage_median": round(statistics.median(wages), 2) if wages else None,
            "wage_p25": round(_percentile(wages, 0.25), 2) if wages else None,
            "wage_p75": round(_percentile(wages, 0.75), 2) if wages else None,
            "top_states": ", ".join(s for s, _ in g["states"].most_common(3)) or None,
            "top_titles": "; ".join(t for t, _ in g["titles"].most_common(3)) or None,
        })
    counts["employers"] = len({s["employer_key"] for s in summaries})
    return summaries, counts


# ---------------------------------------------------------------
# Database
# ---------------------------------------------------------------

_COPY_COLUMNS = [
    "fiscal_year", "employer_key", "employer_name", "role_family", "filings", "positions",
    "wage_median", "wage_p25", "wage_p75", "top_states", "top_titles", "source_file",
]


def save(conn, summaries: list[dict], fiscal_year: int, source_file: str) -> int:
    """Replace that fiscal year's rows with the new summaries (safe to re-run)."""
    with conn.transaction():
        conn.execute("DELETE FROM h1b_filings WHERE fiscal_year = %s", (fiscal_year,))
        with conn.cursor() as cur:
            with cur.copy(f"COPY h1b_filings ({', '.join(_COPY_COLUMNS)}) FROM STDIN") as copy:
                for s in summaries:
                    copy.write_row([fiscal_year if c == "fiscal_year" else source_file if c == "source_file" else s[c]
                                    for c in _COPY_COLUMNS])
    return len(summaries)


def load_aliases() -> dict[str, list[str]]:
    data = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8")) or {}
    return data.get("aliases") or {}


def match_companies(conn) -> dict[str, int]:
    """Link H-1B rows to our companies by cleaned name or alias. Returns {company: filings}."""
    aliases = load_aliases()
    with conn.transaction():
        conn.execute("UPDATE h1b_filings SET company_id = NULL WHERE company_id IS NOT NULL")
        for company_id, name in conn.execute("SELECT id, name FROM companies").fetchall():
            keys = {normalize_employer(name)} | {normalize_employer(a) for a in aliases.get(name, [])}
            conn.execute(
                "UPDATE h1b_filings SET company_id = %s WHERE employer_key = ANY(%s)",
                (company_id, sorted(keys)),
            )
    rows = conn.execute(
        "SELECT c.name, coalesce(sum(h.filings), 0) FROM companies c "
        "LEFT JOIN h1b_filings h ON h.company_id = c.id GROUP BY c.name ORDER BY 2 DESC"
    ).fetchall()
    return dict(rows)


def report(conn) -> None:
    """Print how each company matched, plus similar DOL names that did NOT match (to catch misses)."""
    fy = conn.execute("SELECT max(fiscal_year) FROM h1b_filings").fetchone()[0]
    if fy is None:
        print("No H-1B data loaded yet.")
        return
    print(f"H-1B filings by company, FY {fy} (certified LCAs, all roles):")
    for name, filings, dol_names in conn.execute(
        "SELECT c.name, coalesce(sum(h.filings), 0), string_agg(DISTINCT h.employer_name, '; ') "
        "FROM companies c LEFT JOIN h1b_filings h ON h.company_id = c.id AND h.fiscal_year = %s "
        "GROUP BY c.name ORDER BY 2 DESC", (fy,)
    ).fetchall():
        print(f"  {name:<12} {filings:>6}   {dol_names or '-'}")

    print("\nPossible missed names (contain the company's first word, not matched):")
    for company_id, name in conn.execute("SELECT id, name FROM companies ORDER BY name").fetchall():
        word = normalize_employer(name).split()[0] if normalize_employer(name) else ""
        if len(word) < 4:
            continue                      # too short to search safely (e.g. 'RO')
        for key, total in conn.execute(
            "SELECT employer_key, sum(filings) FROM h1b_filings WHERE fiscal_year = %s AND company_id IS NULL "
            "AND employer_key ~ %s GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
            (fy, rf"\m{re.escape(word)}\M"),
        ).fetchall():
            print(f"  {name:<12} ? {key} ({total})")


# ---------------------------------------------------------------
# Command line
# ---------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Load H-1B history from a DOL LCA disclosure file.")
    parser.add_argument("file", nargs="?", type=Path, help="the .xlsx file in data/h1b/")
    parser.add_argument("--fiscal-year", type=int, help="override the year read from the file name")
    parser.add_argument("--check-columns", action="store_true", help="only show which columns were found")
    parser.add_argument("--report", action="store_true", help="show how our companies matched")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    from pipeline.db import get_connection

    if args.report and not args.file:
        with get_connection() as conn:
            report(conn)
        return
    if not args.file:
        parser.error("give the .xlsx file, or use --report")

    if args.check_columns:
        from openpyxl import load_workbook
        workbook = load_workbook(args.file, read_only=True)
        headers = list(next(workbook.worksheets[0].iter_rows(values_only=True)))
        workbook.close()
        columns, missing = map_columns(headers)
        print(f"{len(headers)} columns in the file.")
        for field in COLUMNS:
            print(f"  {field:<15} -> {headers[columns[field]] if field in columns else 'NOT FOUND'}")
        print("Missing required columns:", missing or "none")
        return

    fiscal_year = args.fiscal_year or fiscal_year_from_filename(args.file)
    if not fiscal_year:
        parser.error("could not read the fiscal year from the file name; use --fiscal-year")
    log.info("Reading %s (fiscal year %d). Large files take several minutes...", args.file.name, fiscal_year)
    summaries, counts = summarize(read_rows(args.file))
    log.info("Rows read: %d, certified H-1B: %d, employers: %d",
             counts["rows"], counts["certified_h1b"], counts["employers"])
    with get_connection() as conn:
        saved = save(conn, summaries, fiscal_year, args.file.name)
        log.info("Saved %d summary rows for FY %d", saved, fiscal_year)
        match_companies(conn)
        report(conn)


if __name__ == "__main__":
    main()
