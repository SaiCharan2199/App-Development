"""Tests for the default search filter jobs_matching() in the local database.

Skipped automatically if the database is not running. Every test runs inside a
transaction that is rolled back, so no test data is ever left behind.
"""

import pytest

pytestmark = pytest.mark.database


@pytest.fixture(scope="module")
def connection():
    psycopg = pytest.importorskip("psycopg")
    from pipeline.db import get_connection
    try:
        conn = get_connection()
    except (psycopg.OperationalError, KeyError) as exc:
        pytest.skip(f"database not available: {exc}")
    yield conn
    conn.close()


@pytest.fixture
def db(connection):
    """A connection inside a transaction that is always rolled back."""
    connection.rollback()
    yield connection
    connection.rollback()


def add_job(db, job_id, **fields):
    defaults = {"country": "US"}
    defaults.update(fields)
    columns = ["source", "source_job_id", "title", *defaults]
    values = ["test", job_id, "Data Engineer", *defaults.values()]
    db.execute(
        f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(values))})",
        values,
    )


def shown(db, call="jobs_matching()"):
    """Test jobs returned by the filter (real jobs already in the database are ignored)."""
    sql = f"SELECT source_job_id FROM {call} WHERE source = 'test'"
    return {row[0] for row in db.execute(sql).fetchall()}


def test_salary_limit(db):
    add_job(db, "range-reaches-60k", salary_max=70000, salary_currency="USD", salary_period="year")
    add_job(db, "below-60k", salary_max=55000, salary_currency="USD", salary_period="year")
    add_job(db, "hourly-above", salary_max=35, salary_currency="USD", salary_period="hour")   # 72,800/yr
    add_job(db, "hourly-below", salary_max=25, salary_currency="USD", salary_period="hour")   # 52,000/yr
    add_job(db, "not-stated")
    assert shown(db) == {"range-reaches-60k", "hourly-above", "not-stated"}


def test_yearly_salary_is_calculated(db):
    add_job(db, "hourly", salary_max=50, salary_period="hour")
    add_job(db, "monthly", salary_max=10000, salary_period="month")
    rows = dict(db.execute("SELECT source_job_id, salary_max_annual FROM jobs WHERE source = 'test'").fetchall())
    assert rows == {"hourly": 104000, "monthly": 120000}


def test_experience_limit(db):
    add_job(db, "five-years", years_experience_min=5)
    add_job(db, "ten-years", years_experience_min=10)
    add_job(db, "zero-years", years_experience_min=0)
    add_job(db, "not-stated")
    assert shown(db) == {"five-years", "zero-years", "not-stated"}


def test_country_limit(db):
    add_job(db, "us")
    add_job(db, "india", country="IN")
    add_job(db, "worldwide-remote", country=None, is_remote=True, remote_worldwide=True)
    add_job(db, "unknown-country", country=None)
    assert shown(db) == {"us", "worldwide-remote"}
    assert shown(db, "jobs_matching(only_country => 'IN')") == {"india", "worldwide-remote"}


def test_sponsorship_needed_by_default(db):
    add_job(db, "sponsors", sponsorship="yes")
    add_job(db, "not-mentioned", sponsorship="unknown")
    add_job(db, "no-sponsorship", sponsorship="no")
    add_job(db, "needs-clearance", clearance_required=True)
    assert shown(db) == {"sponsors", "not-mentioned"}
    everything = shown(db, "jobs_matching(needs_sponsorship => false)")
    assert everything == {"sponsors", "not-mentioned", "no-sponsorship", "needs-clearance"}


@pytest.mark.parametrize("column, bad_value", [
    ("employment_type", "freelance"),
    ("experience_level", "expert"),
    ("sponsorship", "maybe"),
    ("salary_period", "week"),
])
def test_database_rejects_invalid_values(db, column, bad_value):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        add_job(db, "bad", **{column: bad_value})


def test_duplicate_job_is_rejected(db):
    import psycopg
    add_job(db, "same")
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_job(db, "same")
