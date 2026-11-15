# AI Job Market Assistant

A data platform that collects real US tech job postings every day, cleans them, and detects the details job seekers actually filter on but most job sites handle badly: **visa sponsorship**, **contract type (C2C / W2 / 1099)**, **experience level**, **years of experience**, **security clearance**, and **salary**.

> **Status:** Phase 1 (data pipeline) complete. Next: daily scheduling, H-1B sponsorship history, a FastAPI backend, and Claude-powered features (resume matching and an AI job-search agent).

---

## What it does

- **Collects** postings from the public job-board APIs of 38 US tech companies (Greenhouse and Lever): about 9,400 jobs per run.
- **Cleans** them into one standard format: removes HTML, normalises titles ("Sr. Data Eng" → "Senior Data Engineer"), detects country and remote status, and converts hourly or monthly pay to yearly.
- **Detects filters** from the job text, saving the **exact sentence** used as evidence for sponsorship and contract type:

| Filter | Values |
|---|---|
| Employment type | full_time, part_time, contract, internship, temporary, unknown |
| Contract type | any of c2c, w2, 1099 (e.g. "W2 only, no C2C" → `w2`) |
| Experience level | entry, mid, senior, lead, manager, director_plus, unknown |
| Years of experience | number, e.g. "5+ years" → 5 |
| Visa sponsorship | yes, no, unknown (never assumed "yes") |
| Security clearance | required / not mentioned |
| Salary | min / max, currency, period (year, month, hour) and a yearly equivalent |
| Skills | 84 skills in categories (languages, cloud, data engineering, AI/ML...) |

- **Stores** everything in PostgreSQL with constraints that reject bad data, and provides a default search that keeps jobs with unstated details instead of hiding them.

## Architecture

```mermaid
flowchart LR
    A[companies.yaml<br/>38 companies] --> B[Extract<br/>Greenhouse + Lever APIs]
    B --> C[(data/raw/<br/>raw JSON by date)]
    C --> D[Transform<br/>clean + detect filters]
    D --> E[Load<br/>upsert, no duplicates]
    E --> F[(PostgreSQL<br/>in Docker)]
    F --> G["jobs_matching()<br/>default search"]
```

| Step | File | Notes |
|---|---|---|
| Extract | [`pipeline/extract.py`](pipeline/extract.py), [`pipeline/sources/`](pipeline/sources/) | Polite: one request per second with retries. One failing company never stops the run. Raw responses are saved unchanged so Transform can be re-run without downloading again. |
| Transform | [`pipeline/transform.py`](pipeline/transform.py), [`pipeline/rules.py`](pipeline/rules.py) | Each rule is a small pure function, unit-tested on its own. |
| Load | [`pipeline/load.py`](pipeline/load.py) | Upserts on `(source, source_job_id)`, so re-running updates jobs instead of duplicating them. One transaction per run. |
| Orchestration | [`pipeline/run_pipeline.py`](pipeline/run_pipeline.py) | Logs every run (status, counts, errors) to the `pipeline_runs` table. |

### Database

Defined in [`database/schema.sql`](database/schema.sql):

| Table | Holds |
|---|---|
| `companies` | One row per company |
| `jobs` | One row per posting: 29 columns including every filter above |
| `skills` | Skill names and categories |
| `job_skills` | Which job needs which skills (many-to-many) |
| `pipeline_runs` | History log of each pipeline run |

Data quality is enforced in the database itself: unique jobs, valid foreign keys, allowed values only (e.g. `sponsorship IN ('yes','no','unknown')`), and `salary_min <= salary_max`.

### Default search: `jobs_matching()`

All jobs are stored, and filters are applied when searching, so limits can change without losing data.

```sql
SELECT * FROM jobs_matching();                              -- defaults below
SELECT * FROM jobs_matching(100000, 5);                     -- $100k+, at most 5 years required
SELECT * FROM jobs_matching(only_country => 'IN');          -- another country
SELECT * FROM jobs_matching(needs_sponsorship => false);    -- include no-sponsorship jobs
```

| Setting | Default | Rule |
|---|---|---|
| `min_salary` | 60000 | Top of the yearly USD range must reach it. Jobs with no salary stated are kept. |
| `max_years_required` | 8 | Job must not require more years. Jobs with no requirement stated are kept. |
| `only_country` | `'US'` | US jobs plus remote jobs open worldwide |
| `needs_sponsorship` | `true` | Hides jobs that say they won't sponsor a visa or that need a security clearance |

## Results from the first full run (14 Nov 2026)

- **9,367 jobs** from 38 companies; **6,132 in the US**; **5,138** match the default search.
- Salary found in **57%** of postings (thanks to US pay-transparency laws); years of experience in **66%**.
- Sponsorship: **683** postings say yes, **151** say no, and **8,533 (91%) don't say**. That gap is what the planned H-1B history feature addresses.
- Most requested skills: Data Engineer: Python, SQL, ETL, dbt, Spark, Snowflake. AI/ML Engineer: LLMs, machine learning, Python, PyTorch.

**Checking against real data mattered.** Reviewing real postings found rule bugs that hand-written examples didn't, including an export-licence sentence ("without sponsorship for an export license") that wrongly marked **480 jobs** as "no visa sponsorship". Each bug now has a regression test.

## Run it yourself

**Requirements:** Python 3.12, Docker Desktop, Git.

```bash
git clone https://github.com/SaiCharan2199/App-Development.git
cd App-Development

python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt

copy .env.example .env            # then set your own POSTGRES_PASSWORD  (macOS/Linux: cp)
docker compose up -d              # start PostgreSQL
python -m pipeline.init_db        # create the tables
python -m pipeline.run_pipeline   # collect, clean and load the jobs
```

Useful options:

```bash
python -m pipeline.run_pipeline --companies stripe zoox      # only some companies
python -m pipeline.run_pipeline --from-raw 2026-11-14        # re-process saved raw data
python -m pipeline.run_pipeline --dry-run                    # no database writes
```

To add a company, add a line to [`pipeline/config/companies.yaml`](pipeline/config/companies.yaml). To add a skill, edit [`pipeline/config/skills.yaml`](pipeline/config/skills.yaml).

## Tests

```bash
pytest
```

127 tests: rule-by-rule unit tests, transform tests on sample Greenhouse and Lever jobs (no internet needed), and database tests for the search filter and constraints. Database tests run inside a rolled-back transaction, and are skipped automatically if Docker isn't running.

## Roadmap

- [x] **Phase 1:** ETL pipeline, database, filter detection, tests
- [ ] **Phase 1b:** Daily scheduling with Apache Airflow
- [ ] **Phase 1c:** H-1B sponsorship history from public US Department of Labor data, matched to companies
- [ ] **Phase 2:** FastAPI backend (job search, trending skills)
- [ ] **Phase 3:** Claude features: resume matcher, AI job-search agent, and LLM filter detection compared against the rules
- [ ] **Phase 4:** Web frontend, CI with GitHub Actions, deployment

## Tech stack

Python 3.12 · PostgreSQL 16 · Docker · `requests` · `psycopg` · BeautifulSoup · PyYAML · pytest

## Data sources

Job data comes from the public [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) and [Lever Postings API](https://github.com/lever/postings-api), which companies use to publish their openings. The pipeline makes at most one request per second, and each job links back to the original posting.
