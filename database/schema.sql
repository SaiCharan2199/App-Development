-- =============================================================
-- AI Job Market Assistant - database schema (PostgreSQL)
-- Safe to run more than once: every statement uses IF NOT EXISTS.
-- Apply with:  python -m pipeline.init_db
-- =============================================================

-- One row per company. Stored once, referenced by many jobs.
CREATE TABLE IF NOT EXISTS companies (
    id          SERIAL PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per job posting.
CREATE TABLE IF NOT EXISTS jobs (
    id                BIGSERIAL PRIMARY KEY,
    source            TEXT        NOT NULL,          -- which API it came from, e.g. 'remotive'
    source_job_id     TEXT        NOT NULL,          -- the job's ID in that API
    title             TEXT        NOT NULL,          -- title exactly as posted
    title_normalized  TEXT,                          -- cleaned title, e.g. 'Senior Data Engineer'
    company_id        INTEGER     REFERENCES companies(id),
    location          TEXT,                          -- as posted, e.g. 'Austin, TX' or 'Remote - US'
    country           CHAR(2),                       -- ISO code, e.g. 'US', 'IN'; NULL = not detected
    is_remote         BOOLEAN,
    remote_worldwide  BOOLEAN NOT NULL DEFAULT false, -- remote job open to any country

    -- ---- Job filters (detected from the posting) ----
    employment_type   TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (employment_type IN
                        ('full_time', 'part_time', 'contract', 'internship', 'temporary', 'unknown')),
    contract_types    TEXT[] NOT NULL DEFAULT '{}'   -- e.g. {w2,c2c}; empty = not stated
                      CHECK (contract_types <@ ARRAY['c2c', 'w2', '1099']::TEXT[]),
    contract_evidence TEXT,                          -- sentence the contract type came from
    experience_level  TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (experience_level IN
                        ('entry', 'mid', 'senior', 'lead', 'manager', 'director_plus', 'unknown')),
    years_experience_min SMALLINT CHECK (years_experience_min BETWEEN 0 AND 50),
    sponsorship       TEXT NOT NULL DEFAULT 'unknown'  -- never assume 'yes'
                      CHECK (sponsorship IN ('yes', 'no', 'unknown')),
    sponsorship_evidence TEXT,                       -- sentence the sponsorship value came from
    clearance_required BOOLEAN,                      -- security clearance / citizenship required
    extraction_method TEXT NOT NULL DEFAULT 'rules'  -- how the filters were detected
                      CHECK (extraction_method IN ('rules', 'claude')),

    salary_min        NUMERIC(12, 2),                -- as posted (per salary_period)
    salary_max        NUMERIC(12, 2),
    salary_currency   TEXT,
    salary_period     TEXT CHECK (salary_period IN ('year', 'month', 'hour')),
    -- Yearly equivalent, calculated automatically (hourly x 2080 hours, monthly x 12).
    salary_max_annual NUMERIC(12, 2) GENERATED ALWAYS AS (
        CASE salary_period
            WHEN 'hour'  THEN salary_max * 2080
            WHEN 'month' THEN salary_max * 12
            ELSE salary_max
        END
    ) STORED,
    description       TEXT,
    url               TEXT,
    posted_at         TIMESTAMPTZ,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- first time our pipeline saw it
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- most recent time we saw it
    CONSTRAINT uq_jobs_source UNIQUE (source, source_job_id),  -- no duplicate jobs
    CONSTRAINT ck_salary_range CHECK (
        salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max
    )
);

-- One row per skill, e.g. Python (language), AWS (cloud).
CREATE TABLE IF NOT EXISTS skills (
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    category  TEXT
);

-- Links jobs and skills (many-to-many).
CREATE TABLE IF NOT EXISTS job_skills (
    job_id    BIGINT  NOT NULL REFERENCES jobs(id)   ON DELETE CASCADE,
    skill_id  INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, skill_id)
);

-- One row per pipeline run: a history log for monitoring.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'success', 'failed')),
    jobs_extracted  INTEGER,
    jobs_loaded     INTEGER,
    error_message   TEXT
);

-- Indexes: make common searches fast.
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at   ON jobs (posted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_company_id  ON jobs (company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_title_norm  ON jobs (title_normalized);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills (skill_id);
CREATE INDEX IF NOT EXISTS idx_jobs_filters     ON jobs (employment_type, experience_level, sponsorship);
CREATE INDEX IF NOT EXISTS idx_jobs_country     ON jobs (country);
CREATE INDEX IF NOT EXISTS idx_jobs_contract    ON jobs USING GIN (contract_types);

-- =============================================================
-- Default search filter: all jobs are stored; this picks the ones
-- that fit your preferences. Jobs that don't state a salary or
-- years of experience are KEPT (shown as "not stated").
--
--   SELECT * FROM jobs_matching();              -- defaults: US, $60,000+, up to 8 years,
--                                               --           needs visa sponsorship
--   SELECT * FROM jobs_matching(80000, 5);      -- custom limits
--   SELECT * FROM jobs_matching(only_country => 'IN');  -- another country
--   SELECT * FROM jobs_matching(only_country => NULL);  -- all countries
--   SELECT * FROM jobs_matching(needs_sponsorship => false);  -- include no-sponsorship jobs
-- =============================================================
CREATE OR REPLACE FUNCTION jobs_matching(
    min_salary          NUMERIC  DEFAULT 60000,  -- top of the yearly range must reach this (USD)
    max_years_required  INTEGER  DEFAULT 8,      -- job must not require more years than this
    only_country        TEXT     DEFAULT 'US',   -- NULL = every country
    needs_sponsorship   BOOLEAN  DEFAULT true    -- hide jobs that say no sponsorship / need clearance
)
RETURNS SETOF jobs
LANGUAGE sql STABLE
AS $$
    SELECT *
    FROM jobs
    WHERE (NOT needs_sponsorship
           OR (sponsorship <> 'no'                         -- 'yes' and 'unknown' are kept
               AND clearance_required IS NOT TRUE))        -- clearance usually means US citizens only
      AND (only_country IS NULL                            -- all countries
           OR country = only_country                       -- jobs in that country
           OR (is_remote AND remote_worldwide))            -- remote, open to anywhere
      AND (salary_max_annual IS NULL                       -- salary not stated: keep
           OR COALESCE(salary_currency, 'USD') <> 'USD'    -- other currencies: not compared
           OR salary_max_annual >= min_salary)
      AND (years_experience_min IS NULL                    -- experience not stated: keep
           OR years_experience_min <= max_years_required);
$$;
