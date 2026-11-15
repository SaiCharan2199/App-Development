"""Run the whole pipeline: Extract -> Transform -> Load, and log the run.

Examples:
    python -m pipeline.run_pipeline                         # all companies
    python -m pipeline.run_pipeline --companies stripe zoox # just these
    python -m pipeline.run_pipeline --from-raw 2026-11-14   # re-use saved raw data
    python -m pipeline.run_pipeline --dry-run               # no database writes
"""

import argparse
import logging
from collections import Counter
from datetime import date

from pipeline.db import get_connection
from pipeline.extract import extract, load_raw
from pipeline.load import load
from pipeline.transform import transform

log = logging.getLogger("pipeline")


def _summary(records: list[dict]) -> None:
    """Print quick counts so each run can be sanity-checked."""
    def show(label, values):
        counts = Counter(values).most_common()
        print(f"  {label:<18} " + ", ".join(f"{k}: {v}" for k, v in counts))

    print(f"\nTransformed {len(records)} jobs")
    show("country (top)", [r["country"] or "not detected" for r in records][:])
    show("employment_type", [r["employment_type"] for r in records])
    show("experience_level", [r["experience_level"] for r in records])
    show("sponsorship", [r["sponsorship"] for r in records])
    show("contract_types", ["+".join(r["contract_types"]) for r in records if r["contract_types"]])
    print(f"  {'with salary':<18} {sum(r['salary_max'] is not None for r in records)}")
    print(f"  {'with years':<18} {sum(r['years_experience_min'] is not None for r in records)}")
    print(f"  {'clearance':<18} {sum(bool(r['clearance_required']) for r in records)}")


def run(companies: list[str] | None, from_raw: date | None, dry_run: bool) -> None:
    conn = None if dry_run else get_connection()
    run_id = None
    if conn:
        run_id = conn.execute("INSERT INTO pipeline_runs DEFAULT VALUES RETURNING id").fetchone()[0]
        conn.commit()

    try:
        if from_raw:
            batches, failed_companies = load_raw(from_raw), []
            log.info("Loaded %d raw files from %s", len(batches), from_raw)
        else:
            batches, failed_companies = extract(companies)
        extracted = sum(len(b["jobs"]) for b in batches)

        records, failed_jobs = transform(batches)
        _summary(records)

        counts = {"inserted": 0, "updated": 0}
        if conn:
            counts = load(conn, records)

        notes = []
        if failed_companies:
            notes.append(f"companies failed: {', '.join(failed_companies)}")
        if failed_jobs:
            notes.append(f"{failed_jobs} jobs failed to transform")
        if conn:
            conn.execute(
                "UPDATE pipeline_runs SET finished_at = now(), status = 'success', jobs_extracted = %s, "
                "jobs_loaded = %s, error_message = %s WHERE id = %s",
                (extracted, counts["inserted"] + counts["updated"], "; ".join(notes) or None, run_id),
            )
            conn.commit()
        print(f"\nDone. Extracted {extracted}, new {counts['inserted']}, updated {counts['updated']}"
              + (" (dry run: nothing saved)" if dry_run else ""))
        for note in notes:
            print(f"  Note: {note}")
    except Exception as exc:
        if conn:
            conn.rollback()
            conn.execute(
                "UPDATE pipeline_runs SET finished_at = now(), status = 'failed', error_message = %s WHERE id = %s",
                (str(exc)[:1000], run_id),
            )
            conn.commit()
        raise
    finally:
        if conn:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the job data pipeline.")
    parser.add_argument("--companies", nargs="+", help="only these company tokens (from companies.yaml)")
    parser.add_argument("--from-raw", type=date.fromisoformat, help="re-process raw data saved on this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="do not write to the database")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    run(args.companies, args.from_raw, args.dry_run)


if __name__ == "__main__":
    main()
