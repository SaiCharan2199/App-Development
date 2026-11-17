"""LOAD: save transformed records into PostgreSQL.

- Companies and skills are created once and reused.
- Jobs are "upserted": new jobs are inserted; jobs we have seen before are
  updated (and last_seen_at moves forward). Running twice never duplicates.
- Everything happens in one transaction: if anything fails, nothing is half-saved.
"""

import logging

from pipeline.transform import load_skills

log = logging.getLogger(__name__)

JOB_COLUMNS = [
    "source", "source_job_id", "title", "title_normalized", "role_family", "company_id", "location", "country",
    "is_remote", "remote_worldwide", "employment_type", "contract_types", "contract_evidence",
    "experience_level", "years_experience_min", "sponsorship", "sponsorship_evidence",
    "clearance_required", "extraction_method", "salary_min", "salary_max", "salary_currency",
    "salary_period", "description", "url", "posted_at",
]
_UPDATE_COLUMNS = [c for c in JOB_COLUMNS if c not in ("source", "source_job_id")]

UPSERT_JOB_SQL = f"""
    INSERT INTO jobs ({", ".join(JOB_COLUMNS)})
    VALUES ({", ".join(["%s"] * len(JOB_COLUMNS))})
    ON CONFLICT (source, source_job_id) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATE_COLUMNS)},
        last_seen_at = now()
    RETURNING id, (xmax = 0) AS inserted
"""


def _sync_skills(conn) -> dict[str, int]:
    """Make sure every skill in skills.yaml exists; return {name: id}."""
    with conn.cursor() as cur:
        for skill in load_skills():
            cur.execute(
                "INSERT INTO skills (name, category) VALUES (%s, %s) "
                "ON CONFLICT (name) DO UPDATE SET category = EXCLUDED.category",
                (skill["name"], skill.get("category")),
            )
        return dict(cur.execute("SELECT name, id FROM skills").fetchall())


def _company_id(conn, cache: dict[str, int], name: str) -> int:
    if name not in cache:
        cache[name] = conn.execute(
            "INSERT INTO companies (name) VALUES (%s) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (name,),
        ).fetchone()[0]
    return cache[name]


def load(conn, records: list[dict]) -> dict[str, int]:
    """Save records using an open connection. Returns {'inserted': n, 'updated': n}."""
    counts = {"inserted": 0, "updated": 0}
    with conn.transaction():
        skill_ids = _sync_skills(conn)
        companies: dict[str, int] = {}
        with conn.cursor() as cur:
            for record in records:
                row = dict(record, company_id=_company_id(conn, companies, record["company"]))
                job_id, inserted = cur.execute(UPSERT_JOB_SQL, [row[c] for c in JOB_COLUMNS]).fetchone()
                counts["inserted" if inserted else "updated"] += 1

                # Replace this job's skills with the ones just detected.
                cur.execute("DELETE FROM job_skills WHERE job_id = %s", (job_id,))
                ids = [skill_ids[s] for s in record["skills"] if s in skill_ids]
                if ids:
                    cur.executemany(
                        "INSERT INTO job_skills (job_id, skill_id) VALUES (%s, %s)",
                        [(job_id, sid) for sid in ids],
                    )
    log.info("Loaded %d new and %d updated jobs", counts["inserted"], counts["updated"])
    return counts
