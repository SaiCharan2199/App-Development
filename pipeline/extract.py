"""EXTRACT: download jobs from every company in config/companies.yaml.

Raw responses are saved unchanged to data/raw/<YYYY-MM-DD>/<ats>_<token>.json,
so Transform can be re-run later without downloading again.
"""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from pipeline.sources import SOURCES

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = PROJECT_ROOT / "pipeline" / "config" / "companies.yaml"
RAW_DIR = PROJECT_ROOT / "data" / "raw"


def load_companies(only: list[str] | None = None) -> list[dict]:
    """Read the company list; optionally keep only the given tokens."""
    companies = yaml.safe_load(COMPANIES_FILE.read_text(encoding="utf-8"))["companies"]
    if only:
        wanted = {t.lower() for t in only}
        companies = [c for c in companies if c["token"].lower() in wanted]
    return companies


def extract(only: list[str] | None = None, run_date: date | None = None) -> tuple[list[dict], list[str]]:
    """Download every company's jobs. Returns (batches, failed_companies).

    Each batch is {"company", "ats", "token", "fetched_at", "jobs": [...]}.
    One company failing does not stop the others.
    """
    run_date = run_date or date.today()
    out_dir = RAW_DIR / run_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    batches, failed = [], []
    for company in load_companies(only):
        name, ats, token = company["name"], company["ats"], company["token"]
        try:
            jobs = SOURCES[ats].fetch_jobs(token)
        except Exception as exc:  # keep going if one board is down or renamed
            log.warning("Extract failed for %s (%s/%s): %s", name, ats, token, exc)
            failed.append(name)
            continue

        batch = {
            "company": name,
            "ats": ats,
            "token": token,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "jobs": jobs,
        }
        (out_dir / f"{ats}_{token}.json").write_text(json.dumps(batch), encoding="utf-8")
        log.info("Extracted %4d jobs from %s", len(jobs), name)
        batches.append(batch)
    return batches, failed


def cleanup_raw(keep_days: int = 14, today: date | None = None, raw_dir: Path = RAW_DIR) -> list[str]:
    """Delete raw data folders older than keep_days. Returns the folder names removed."""
    import shutil

    today = today or date.today()
    removed = []
    for folder in sorted(raw_dir.iterdir()) if raw_dir.exists() else []:
        try:
            folder_date = date.fromisoformat(folder.name)
        except ValueError:
            continue                      # not a dated folder (e.g. .gitkeep): leave it alone
        if folder.is_dir() and (today - folder_date).days > keep_days:
            shutil.rmtree(folder)
            removed.append(folder.name)
    return removed


def load_raw(run_date: date) -> list[dict]:
    """Read back raw batches saved on a given date (to re-run Transform)."""
    folder = RAW_DIR / run_date.isoformat()
    if not folder.exists():
        raise FileNotFoundError(f"No raw data saved for {run_date} in {folder}")
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
