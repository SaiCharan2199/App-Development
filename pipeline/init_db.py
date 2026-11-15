"""Create the database tables from database/schema.sql.

Safe to run more than once (the schema uses IF NOT EXISTS).
Run:
    python -m pipeline.init_db
"""

from pathlib import Path

from pipeline.db import get_connection

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "database" / "schema.sql"


def init_db() -> list[str]:
    """Apply the schema and return the names of the tables that now exist."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.execute(sql)
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ).fetchall()
    return [row[0] for row in rows]


if __name__ == "__main__":
    tables = init_db()
    print(f"Schema applied. Tables in the database ({len(tables)}):")
    for name in tables:
        print(f"  - {name}")
