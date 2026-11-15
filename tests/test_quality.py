"""Tests for pipeline/quality.py (rules) and raw-file cleanup. No database needed."""

from datetime import date

import pytest

from pipeline.extract import cleanup_raw
from pipeline.quality import evaluate

GOOD = {
    "jobs_loaded": 9000,
    "companies_total": 38,
    "companies_failed": 1,
    "previous_jobs_loaded": 9300,
    "duplicate_jobs": 0,
    "jobs_missing_required": 0,
    "jobs_seen_this_run": 9000,
    "unknown_country_this_run": 20,
}


def failed_checks(metrics):
    return {r.name for r in evaluate(metrics) if not r.passed}


def test_healthy_run_passes_every_check():
    assert failed_checks(GOOD) == set()


@pytest.mark.parametrize("change, failing_check", [
    ({"jobs_loaded": 0}, "jobs_loaded"),
    ({"duplicate_jobs": 3}, "no_duplicates"),
    ({"jobs_missing_required": 1}, "required_fields"),
    ({"companies_failed": 12}, "companies_reachable"),          # 12 of 38 = 32%
    ({"jobs_loaded": 4000}, "no_sudden_drop"),                  # 43% of last run
    ({"unknown_country_this_run": 900}, "country_detected"),    # 10%
])
def test_each_problem_is_caught(change, failing_check):
    assert failing_check in failed_checks({**GOOD, **change})


def test_first_run_has_nothing_to_compare_with():
    results = {r.name: r for r in evaluate({**GOOD, "previous_jobs_loaded": None})}
    assert results["no_sudden_drop"].passed


def test_small_drop_is_allowed():
    assert failed_checks({**GOOD, "jobs_loaded": 8000}) == set()   # 86% of last run


def test_cleanup_keeps_recent_raw_folders(tmp_path):
    for name in ["2026-10-01", "2026-11-01", "2026-11-14", "2026-11-15"]:
        (tmp_path / name).mkdir()
    (tmp_path / ".gitkeep").touch()

    removed = cleanup_raw(keep_days=14, today=date(2026, 11, 15), raw_dir=tmp_path)

    assert removed == ["2026-10-01"]                       # 45 days old
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [".gitkeep", "2026-11-01", "2026-11-14", "2026-11-15"]   # 14 days old is kept
