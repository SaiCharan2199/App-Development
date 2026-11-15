"""Data quality checks run after each load.

Split in two so the rules can be tested without a database:
  - gather_metrics(conn, run_stats) -> dict of numbers read from the database
  - evaluate(metrics)               -> list of CheckResult (pure, no database)

If any check fails, the scheduled run is marked as failed instead of quietly
saving bad data.
"""

from dataclasses import dataclass

# Thresholds: change here if a rule is too strict or too loose.
MAX_FAILED_COMPANY_SHARE = 0.25     # more than 25% of companies failing = a real problem
MIN_SHARE_OF_PREVIOUS_RUN = 0.50    # jobs seen today must be at least half of the last run
MAX_UNKNOWN_COUNTRY_SHARE = 0.05    # country detection should work for 95%+ of jobs


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def gather_metrics(conn, run_stats: dict) -> dict:
    """Read the numbers the checks need. run_stats comes from run_pipeline.run()."""
    one = lambda sql, params=(): conn.execute(sql, params).fetchone()[0]
    previous = conn.execute(
        "SELECT jobs_loaded FROM pipeline_runs WHERE status = 'success' AND id <> %s "
        "ORDER BY id DESC LIMIT 1",
        (run_stats.get("run_id") or -1,),
    ).fetchone()
    return {
        "jobs_loaded": run_stats.get("inserted", 0) + run_stats.get("updated", 0),
        "companies_total": run_stats.get("companies_total", 0),
        "companies_failed": len(run_stats.get("failed_companies", [])),
        "previous_jobs_loaded": previous[0] if previous else None,
        "duplicate_jobs": one(
            "SELECT count(*) FROM (SELECT 1 FROM jobs GROUP BY source, source_job_id HAVING count(*) > 1) d"
        ),
        "jobs_missing_required": one(
            "SELECT count(*) FROM jobs WHERE company_id IS NULL OR title = '' OR url IS NULL"
        ),
        "jobs_seen_this_run": one("SELECT count(*) FROM jobs WHERE last_seen_at >= now() - interval '1 day'"),
        "unknown_country_this_run": one(
            "SELECT count(*) FROM jobs WHERE country IS NULL AND last_seen_at >= now() - interval '1 day'"
        ),
    }


def evaluate(m: dict) -> list[CheckResult]:
    """Apply the rules to the metrics. Pure function: easy to unit-test."""
    results = [
        CheckResult("jobs_loaded", m["jobs_loaded"] > 0, f"{m['jobs_loaded']} jobs loaded"),
        CheckResult("no_duplicates", m["duplicate_jobs"] == 0, f"{m['duplicate_jobs']} duplicate jobs"),
        CheckResult(
            "required_fields", m["jobs_missing_required"] == 0,
            f"{m['jobs_missing_required']} jobs missing company, title or URL",
        ),
    ]

    total = m["companies_total"]
    share = m["companies_failed"] / total if total else 0
    results.append(CheckResult(
        "companies_reachable", share <= MAX_FAILED_COMPANY_SHARE,
        f"{m['companies_failed']} of {total} companies failed ({share:.0%})",
    ))

    previous = m["previous_jobs_loaded"]
    if previous:
        ratio = m["jobs_loaded"] / previous
        results.append(CheckResult(
            "no_sudden_drop", ratio >= MIN_SHARE_OF_PREVIOUS_RUN,
            f"{m['jobs_loaded']} jobs vs {previous} last run ({ratio:.0%})",
        ))
    else:
        results.append(CheckResult("no_sudden_drop", True, "first run: nothing to compare with"))

    seen = m["jobs_seen_this_run"]
    unknown = m["unknown_country_this_run"] / seen if seen else 0
    results.append(CheckResult(
        "country_detected", unknown <= MAX_UNKNOWN_COUNTRY_SHARE,
        f"{m['unknown_country_this_run']} of {seen} jobs with no country ({unknown:.1%})",
    ))
    return results


def run_checks(conn, run_stats: dict) -> list[CheckResult]:
    """Gather metrics and evaluate them. Raises ValueError if any check fails."""
    results = evaluate(gather_metrics(conn, run_stats))
    failed = [r for r in results if not r.passed]
    if failed:
        raise ValueError("Data quality checks failed: " + "; ".join(f"{r.name} ({r.detail})" for r in failed))
    return results
