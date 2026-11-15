"""Lever Postings API (public, no key needed).

Docs: https://github.com/lever/postings-api
"""

from pipeline.sources.http import get_json

BASE_URL = "https://api.lever.co/v0/postings/{token}"
PAGE_SIZE = 100


def fetch_jobs(token: str) -> list[dict]:
    """Return all published postings for one company, page by page."""
    jobs: list[dict] = []
    skip = 0
    while True:
        page = get_json(
            BASE_URL.format(token=token),
            params={"mode": "json", "skip": skip, "limit": PAGE_SIZE},
        )
        if not isinstance(page, list) or not page:
            break
        jobs.extend(page)
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    return jobs
