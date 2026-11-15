-- Runs automatically only when the PostgreSQL volume is brand new.
-- Airflow keeps its own bookkeeping (runs, task states) in this separate database,
-- away from the job data in jobs_db.
CREATE DATABASE airflow;
