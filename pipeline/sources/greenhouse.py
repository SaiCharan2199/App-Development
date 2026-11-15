"""Greenhouse Job Board API (public, no key needed).

Docs: https://docs.greenhouse.io/job-board.html
"""

from pipeline.sources.http import get_json

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch_jobs(token: str) -> list[dict]:
    """Return all published jobs for one company, including full descriptions."""
    data = get_json(BASE_URL.format(token=token), params={"content": "true"})
    return data.get("jobs", [])
