"""Shared database helper: opens a connection to PostgreSQL using settings from .env.

Run this file directly to test the connection:
    python -m pipeline.db
"""

import os

import psycopg
from dotenv import load_dotenv

# Read settings from the .env file in the project folder.
load_dotenv()


def get_connection() -> psycopg.Connection:
    """Open and return a new connection to the project's PostgreSQL database."""
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


if __name__ == "__main__":
    with get_connection() as conn:
        version = conn.execute("SELECT version()").fetchone()[0]
        database = conn.execute("SELECT current_database()").fetchone()[0]
    print("Connected to the database successfully!")
    print(f"Database: {database}")
    print(f"Server:   {version.split(',')[0]}")
