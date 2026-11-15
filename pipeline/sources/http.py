"""Shared HTTP settings so every source behaves politely and the same way."""

import time

import requests

HEADERS = {"User-Agent": "job-market-assistant/0.1 (personal portfolio project)"}
TIMEOUT_SECONDS = 60
PAUSE_SECONDS = 1.0   # wait between requests so we never overload a job board
RETRIES = 3


def get_json(url: str, params: dict | None = None):
    """GET a URL and return its JSON, retrying briefly on temporary errors."""
    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                time.sleep(PAUSE_SECONDS * attempt * 5)
                continue
            response.raise_for_status()
            return response.json()
        except requests.ConnectionError:
            if attempt == RETRIES:
                raise
            time.sleep(PAUSE_SECONDS * attempt * 5)
        finally:
            time.sleep(PAUSE_SECONDS)
