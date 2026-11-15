"""TRANSFORM: turn raw Greenhouse/Lever jobs into one clean, standard format.

Each output record has exactly the columns of the `jobs` table, plus:
  - "company": the company name (Load turns it into company_id)
  - "skills":  list of skill names found in the posting
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from pipeline import rules

log = logging.getLogger(__name__)

SKILLS_FILE = Path(__file__).resolve().parent / "config" / "skills.yaml"

_LEVER_INTERVALS = {
    "per-year-salary": "year",
    "per-month-salary": "month",
    "per-hour-wage": "hour",
}


def load_skills() -> list[dict]:
    return yaml.safe_load(SKILLS_FILE.read_text(encoding="utf-8"))["skills"]


def _parse_timestamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):                       # Lever: milliseconds since 1970
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------
# Source-specific: pull the raw fields out of each API's format
# ---------------------------------------------------------------


def _greenhouse_metadata(job: dict) -> dict[str, str]:
    """Company-defined custom fields as {lowercase name: text value}."""
    meta = {}
    for m in job.get("metadata") or []:
        value = m.get("value")
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value if v)
        if isinstance(value, str) and value.strip():
            meta[(m.get("name") or "").strip().lower()] = value.strip()
    return meta


def _greenhouse_fields(job: dict) -> dict:
    offices = [o.get("name") for o in job.get("offices") or [] if o.get("name")]
    location = (job.get("location") or {}).get("name") or "; ".join(offices)
    meta = _greenhouse_metadata(job)
    employment_hint = next(
        (meta[k] for k in ("employment type", "employee type", "worker type", "time type") if k in meta), None
    )
    workplace = next((meta[k] for k in ("workplace type", "location type", "remote status") if k in meta), None)
    # Some companies put only 'Hybrid' or 'Remote' in location and the real city
    # in offices or a custom field, so country detection looks at all of them.
    location_detail = "; ".join(
        [location, *offices, *(meta[k] for k in ("job posting location", "posting location", "country") if k in meta)]
    )
    return {
        "source_job_id": str(job["id"]),
        "title": job.get("title") or "",
        "location": location,
        "location_detail": location_detail,
        "country_hint": None,
        "workplace_type": workplace,
        "text": rules.html_to_text(job.get("content")),
        "url": job.get("absolute_url"),
        "posted_at": _parse_timestamp(job.get("first_published") or job.get("updated_at")),
        "employment_hint": employment_hint,
        "salary": None,
    }


def _lever_fields(job: dict) -> dict:
    categories = job.get("categories") or {}
    location = categories.get("location") or "; ".join(categories.get("allLocations") or [])
    parts = [job.get("descriptionPlain") or ""]
    for section in job.get("lists") or []:
        parts.append(section.get("text") or "")
        parts.append(rules.html_to_text(section.get("content")))
    parts.append(job.get("additionalPlain") or "")
    parts.append(job.get("salaryDescriptionPlain") or "")

    salary = None
    sr = job.get("salaryRange") or {}
    period = _LEVER_INTERVALS.get(sr.get("interval"))
    if period and sr.get("min") is not None and sr.get("max") is not None and sr["min"] <= sr["max"]:
        salary = {"min": float(sr["min"]), "max": float(sr["max"]),
                  "currency": sr.get("currency") or "USD", "period": period}

    return {
        "source_job_id": str(job["id"]),
        "title": job.get("text") or "",
        "location": location,
        "location_detail": location,
        "country_hint": (job.get("country") or "").upper() or None,
        "workplace_type": job.get("workplaceType"),
        "text": "\n".join(p for p in parts if p),
        "url": job.get("hostedUrl"),
        "posted_at": _parse_timestamp(job.get("createdAt")),
        "employment_hint": categories.get("commitment"),
        "salary": salary,
    }


_FIELD_EXTRACTORS = {"greenhouse": _greenhouse_fields, "lever": _lever_fields}


# ---------------------------------------------------------------
# Common: apply the rules to build the final record
# ---------------------------------------------------------------


def build_record(source: str, company: str, f: dict, skill_patterns) -> dict:
    title, text = f["title"], f["text"]

    employment_type = rules.detect_employment_type(title, text, f["employment_hint"])
    contract_types, contract_evidence = rules.detect_contract_types(text)
    if contract_types and employment_type != "contract":
        if set(contract_types) & {"c2c", "1099"}:
            employment_type = "contract"          # C2C/1099 only exist for contracts
        else:
            contract_types, contract_evidence = [], None   # 'W2' alone on a normal job means nothing extra

    years = rules.detect_years_experience(text)
    sponsorship, sponsorship_evidence = rules.detect_sponsorship(text)
    salary = f["salary"] or rules.parse_salary(text)
    is_remote, remote_worldwide = rules.detect_remote(f["location_detail"], f["workplace_type"])
    hint = f["country_hint"]
    country = hint if hint and len(hint) == 2 else rules.detect_country(f["location_detail"])

    return {
        "source": source,
        "source_job_id": f["source_job_id"],
        "company": company,
        "title": title,
        "title_normalized": rules.normalize_title(title),
        "role_family": rules.detect_role_family(title),
        "location": f["location"] or None,
        "country": country,
        "is_remote": is_remote,
        "remote_worldwide": remote_worldwide,
        "employment_type": employment_type,
        "contract_types": contract_types,
        "contract_evidence": contract_evidence,
        "experience_level": rules.detect_experience_level(title, years),
        "years_experience_min": years,
        "sponsorship": sponsorship,
        "sponsorship_evidence": sponsorship_evidence,
        "clearance_required": rules.detect_clearance(text),
        "extraction_method": "rules",
        "salary_min": salary["min"] if salary else None,
        "salary_max": salary["max"] if salary else None,
        "salary_currency": salary["currency"] if salary else None,
        "salary_period": salary["period"] if salary else None,
        "description": text or None,
        "url": f["url"],
        "posted_at": f["posted_at"],
        "skills": rules.detect_skills(f"{title}\n{text}", skill_patterns),
    }


def transform(batches: list[dict]) -> tuple[list[dict], int]:
    """Transform all raw batches. Returns (records, number_of_jobs_that_failed)."""
    skill_patterns = rules.compile_skill_patterns(load_skills())
    records: dict[tuple[str, str], dict] = {}   # keyed by (source, source_job_id): removes duplicates
    failed = 0
    for batch in batches:
        extractor = _FIELD_EXTRACTORS[batch["ats"]]
        for job in batch["jobs"]:
            try:
                record = build_record(batch["ats"], batch["company"], extractor(job), skill_patterns)
            except Exception as exc:
                failed += 1
                log.warning("Transform failed for a %s job (%s): %s", batch["company"], job.get("id"), exc)
                continue
            records[(record["source"], record["source_job_id"])] = record
    return list(records.values()), failed
