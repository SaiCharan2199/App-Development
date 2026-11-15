"""Daily job market pipeline, scheduled by Apache Airflow.

    extract_jobs -> transform_and_load -> check_data_quality -> report_summary
                                                            \\-> cleanup_old_raw_files

Each task reuses the code in pipeline/, so the same pipeline also runs by hand:
    python -m pipeline.run_pipeline
"""

import logging
from datetime import date, timedelta

import pendulum
from airflow.sdk import dag, task

log = logging.getLogger(__name__)


@dag(
    dag_id="job_market_pipeline",
    description="Collect US tech jobs, detect filters, load to PostgreSQL, check quality",
    schedule="0 13 * * *",                       # every day at 13:00 UTC
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,                               # don't run for days that were missed
    max_active_runs=1,                           # never two runs at the same time
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["jobs", "etl"],
)
def job_market_pipeline():

    @task
    def extract_jobs() -> dict:
        """Download every company's jobs and save the raw files."""
        from pipeline.extract import extract

        run_date = date.today()
        batches, failed = extract(run_date=run_date)
        if not batches:
            raise RuntimeError("No company could be downloaded")
        jobs = sum(len(b["jobs"]) for b in batches)
        log.info("Downloaded %d jobs from %d companies (%d failed: %s)",
                 jobs, len(batches), len(failed), ", ".join(failed) or "none")
        return {
            "run_date": run_date.isoformat(),
            "jobs": jobs,
            "companies_total": len(batches) + len(failed),
            "failed_companies": failed,
        }

    @task
    def transform_and_load(info: dict) -> dict:
        """Clean the saved raw files, detect filters and save to the database."""
        from pipeline.run_pipeline import run

        stats = run(None, from_raw=date.fromisoformat(info["run_date"]), dry_run=False)
        stats["companies_total"] = info["companies_total"]
        stats["failed_companies"] = info["failed_companies"]
        return stats

    @task(retries=0)                             # a quality failure needs a person, not a retry
    def check_data_quality(stats: dict) -> dict:
        """Fail the run if the data looks wrong."""
        from pipeline.db import get_connection
        from pipeline.quality import run_checks

        with get_connection() as conn:
            results = run_checks(conn, stats)    # raises if any check fails
        for r in results:
            log.info("PASS  %-20s %s", r.name, r.detail)
        return stats

    @task
    def report_summary(stats: dict) -> None:
        """Log today's numbers so each run is easy to read in the Airflow UI."""
        from pipeline.db import get_connection

        with get_connection() as conn:
            one = lambda sql: conn.execute(sql).fetchone()[0]
            summary = {
                "new jobs this run": stats["inserted"],
                "updated jobs": stats["updated"],
                "total jobs stored": one("SELECT count(*) FROM jobs"),
                "match default search": one("SELECT count(*) FROM jobs_matching()"),
                "say they sponsor (US)": one("SELECT count(*) FROM jobs WHERE sponsorship = 'yes' AND country = 'US'"),
                "new today in default search": one(
                    "SELECT count(*) FROM jobs_matching() WHERE first_seen_at >= now() - interval '1 day'"
                ),
            }
        for label, value in summary.items():
            log.info("%-28s %s", label, value)

    @task
    def cleanup_old_raw_files() -> None:
        """Keep only the last 14 days of raw files so the disk does not fill up."""
        from pipeline.extract import cleanup_raw

        removed = cleanup_raw(keep_days=14)
        log.info("Removed %d old raw folders: %s", len(removed), ", ".join(removed) or "none")

    checked = check_data_quality(transform_and_load(extract_jobs()))
    report_summary(checked)
    checked >> cleanup_old_raw_files()


job_market_pipeline()
