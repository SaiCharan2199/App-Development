"""Tests for pipeline/transform.py using small hand-written sample jobs (no internet)."""

from pipeline.load import JOB_COLUMNS
from pipeline.transform import transform

GREENHOUSE_JOB = {
    "id": 111,
    "title": "Sr. Data Engineer",
    "location": {"name": "Austin, TX"},
    "absolute_url": "https://example.com/jobs/111",
    "first_published": "2026-11-01T09:00:00-05:00",
    "metadata": [{"name": "Employee Type", "value": "Full-time"}],
    "content": (
        "&lt;p&gt;You have 5+ years of experience with Python, SQL and Airflow.&lt;/p&gt;"
        "&lt;p&gt;The base salary range is $150,000 - $180,000 USD.&lt;/p&gt;"
        "&lt;p&gt;Visa sponsorship is available for this position.&lt;/p&gt;"
    ),
}

LEVER_JOB = {
    "id": "abc-123",
    "text": "Machine Learning Engineer",
    "categories": {"commitment": "Contract", "location": "Remote - US"},
    "country": "US",
    "workplaceType": "remote",
    "createdAt": 1793000000000,
    "hostedUrl": "https://example.com/jobs/abc-123",
    "descriptionPlain": "Build models with PyTorch. 3+ years of experience required.",
    "lists": [{"text": "Requirements", "content": "<li>Open to W2 or C2C candidates.</li>"}],
    "additionalPlain": "You must be a U.S. citizen.",
    "salaryRange": {"min": 60, "max": 75, "currency": "USD", "interval": "per-hour-wage"},
}


def _batch(ats, jobs, company="TestCo"):
    return {"company": company, "ats": ats, "token": "test", "jobs": jobs}


def _one(ats, job):
    records, failed = transform([_batch(ats, [job])])
    assert failed == 0
    assert len(records) == 1
    return records[0]


def test_record_has_every_jobs_column():
    record = _one("greenhouse", GREENHOUSE_JOB)
    for column in JOB_COLUMNS:
        if column != "company_id":                 # added by Load
            assert column in record, column
    assert "company" in record and "skills" in record


def test_greenhouse_job_is_transformed():
    r = _one("greenhouse", GREENHOUSE_JOB)
    assert r["source"] == "greenhouse"
    assert r["source_job_id"] == "111"
    assert r["title_normalized"] == "Senior Data Engineer"
    assert r["country"] == "US"
    assert r["is_remote"] is False
    assert r["employment_type"] == "full_time"
    assert r["experience_level"] == "senior"
    assert r["years_experience_min"] == 5
    assert (r["salary_min"], r["salary_max"], r["salary_period"]) == (150000, 180000, "year")
    assert r["sponsorship"] == "yes"
    assert "sponsorship" in r["sponsorship_evidence"].lower()
    assert r["skills"] == ["Apache Airflow", "Python", "SQL"]
    assert "<p>" not in r["description"]            # HTML removed
    assert r["posted_at"].year == 2026


def test_lever_job_uses_structured_fields():
    r = _one("lever", LEVER_JOB)
    assert r["source_job_id"] == "abc-123"
    assert r["employment_type"] == "contract"          # from 'commitment'
    assert r["contract_types"] == ["c2c", "w2"]
    assert r["country"] == "US"                        # from Lever's country field
    assert (r["is_remote"], r["remote_worldwide"]) == (True, False)
    assert (r["salary_min"], r["salary_max"], r["salary_period"]) == (60, 75, "hour")
    assert r["sponsorship"] == "no"                    # 'must be a U.S. citizen'
    assert r["years_experience_min"] == 3
    assert "PyTorch" in r["skills"]


def test_w2_on_a_normal_full_time_job_is_ignored():
    job = dict(GREENHOUSE_JOB, id=222, content="&lt;p&gt;This is a full-time W2 position.&lt;/p&gt;")
    r = _one("greenhouse", job)
    assert r["employment_type"] == "full_time"
    assert r["contract_types"] == []


def test_c2c_mention_makes_job_a_contract():
    job = dict(GREENHOUSE_JOB, id=333, metadata=[], content="&lt;p&gt;Open to C2C candidates.&lt;/p&gt;")
    r = _one("greenhouse", job)
    assert r["employment_type"] == "contract"
    assert r["contract_types"] == ["c2c"]


def test_duplicate_jobs_are_removed():
    records, _ = transform([_batch("greenhouse", [GREENHOUSE_JOB, GREENHOUSE_JOB])])
    assert len(records) == 1


def test_one_broken_job_does_not_stop_the_others():
    broken = {"title": "No id field"}             # missing 'id'
    records, failed = transform([_batch("greenhouse", [broken, GREENHOUSE_JOB])])
    assert failed == 1
    assert len(records) == 1


def test_greenhouse_city_from_offices_and_custom_fields():
    # Real pattern (e.g. Cloudflare): location says only 'Hybrid'; the city is elsewhere.
    job = dict(
        GREENHOUSE_JOB, id=555,
        location={"name": "Hybrid"},
        offices=[{"name": "Washington, DC"}],
        metadata=[{"name": "Job Posting Location", "value": ["Washington DC, US"]},
                  {"name": "Workplace Type", "value": "Hybrid"}],
    )
    r = _one("greenhouse", job)
    assert r["location"] == "Hybrid"          # shown as posted
    assert r["country"] == "US"               # detected from offices / custom field
    assert r["is_remote"] is False


def test_greenhouse_custom_employment_and_workplace_fields():
    job = dict(
        GREENHOUSE_JOB, id=666,
        location={"name": "Remote"}, offices=[{"name": "United States"}],
        metadata=[{"name": "Employment Type", "value": "Contractor"},
                  {"name": "Workplace Type", "value": "Remote"}],
    )
    r = _one("greenhouse", job)
    assert r["employment_type"] == "contract"
    assert (r["is_remote"], r["country"]) == (True, "US")


def test_missing_values_become_unknown_not_guesses():
    job = {"id": 444, "title": "Data Engineer", "location": {"name": "Remote"}, "content": ""}
    r = _one("greenhouse", job)
    assert r["sponsorship"] == "unknown"
    assert r["employment_type"] == "unknown"
    assert r["experience_level"] == "unknown"
    assert r["salary_max"] is None
    assert r["country"] is None
