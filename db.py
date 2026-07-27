"""Persistence layer. Two backends, chosen automatically:

- No DATABASE_URL set -> local SQLite file (data/job_hunter.db). Good for
  solo local development.
- DATABASE_URL set (a Postgres connection string, e.g. from Supabase) ->
  Postgres. This is what lets the locally-scheduled scraper and the
  cloud-hosted (Streamlit Community Cloud) dashboard share one live
  database -- a local SQLite file can't be reached from a cloud process.

Every function below is written once and works against either backend: a
thin wrapper gives Postgres connections the same conn.execute(sql, params)
shape sqlite3 already has, with '?' placeholders auto-translated to '%s'.
UTF-8 (Hebrew-safe) throughout in both cases.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)

if IS_PG:
    import psycopg2
    import psycopg2.extras

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    domain TEXT,
    contact_email TEXT,
    contact_email_confidence TEXT,
    industry TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    job_title TEXT NOT NULL,
    job_url TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    language TEXT,
    raw_description TEXT,
    posted_date TEXT,
    required_tech_stack TEXT,
    seniority_level TEXT,
    contact_email TEXT,
    contact_name TEXT,
    fit_score INTEGER,
    status TEXT NOT NULL DEFAULT 'NEW',
    outreach_initiated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    contact_person TEXT,
    contact_email TEXT,
    language TEXT,
    email_subject TEXT,
    email_body TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_fit_score ON jobs(fit_score);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
"""

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    domain TEXT,
    contact_email TEXT,
    contact_email_confidence TEXT,
    industry TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    job_title TEXT NOT NULL,
    job_url TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    language TEXT,
    raw_description TEXT,
    posted_date TEXT,
    required_tech_stack TEXT,
    seniority_level TEXT,
    contact_email TEXT,
    contact_name TEXT,
    fit_score INTEGER,
    status TEXT NOT NULL DEFAULT 'NEW',
    outreach_initiated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_logs (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    contact_person TEXT,
    contact_email TEXT,
    language TEXT,
    email_subject TEXT,
    email_body TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_fit_score ON jobs(fit_score);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _PGConnWrapper:
    """Makes a psycopg2 connection quack like sqlite3's: conn.execute(sql,
    params) returning a cursor, with '?' placeholders auto-translated."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def executescript(self, sql: str):
        cur = self._conn.cursor()
        cur.execute(sql)
        cur.close()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


@contextmanager
def get_conn():
    if IS_PG:
        raw = psycopg2.connect(DATABASE_URL)
        conn = _PGConnWrapper(raw)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA_PG if IS_PG else SCHEMA_SQLITE)


def get_or_create_company(conn, name: str, domain: str | None = None) -> int:
    row = conn.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()
    if row:
        if domain:
            conn.execute("UPDATE companies SET domain = COALESCE(domain, ?) WHERE id = ?", (domain, row["id"]))
        return row["id"]
    if IS_PG:
        row = conn.execute(
            "INSERT INTO companies (name, domain, created_at) VALUES (?, ?, ?) RETURNING id",
            (name, domain, now_iso()),
        ).fetchone()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO companies (name, domain, created_at) VALUES (?, ?, ?)",
        (name, domain, now_iso()),
    )
    return cur.lastrowid


def job_exists(conn, job_url: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE job_url = ?", (job_url,)).fetchone() is not None


def insert_job(conn, *, company_id: int, job_title: str, job_url: str, source: str,
                language: str | None = None, raw_description: str = "", posted_date: str | None = None) -> int:
    ts = now_iso()
    if IS_PG:
        row = conn.execute(
            """INSERT INTO jobs
               (company_id, job_title, job_url, source, language, raw_description,
                posted_date, required_tech_stack, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'NEW', ?, ?)
               ON CONFLICT (job_url) DO NOTHING
               RETURNING id""",
            (company_id, job_title, job_url, source, language, raw_description, posted_date, ts, ts),
        ).fetchone()
        if row:
            return row["id"]
        return conn.execute("SELECT id FROM jobs WHERE job_url = ?", (job_url,)).fetchone()["id"]

    cur = conn.execute(
        """INSERT OR IGNORE INTO jobs
           (company_id, job_title, job_url, source, language, raw_description,
            posted_date, required_tech_stack, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'NEW', ?, ?)""",
        (company_id, job_title, job_url, source, language, raw_description, posted_date, ts, ts),
    )
    if cur.rowcount == 0:
        row = conn.execute("SELECT id FROM jobs WHERE job_url = ?", (job_url,)).fetchone()
        return row["id"]
    return cur.lastrowid


def finalize_job_core_fields(conn, job_id: int, *, company_name: str | None, job_title: str | None):
    """LLM parsing often extracts a cleaner company_name/job_title than a
    generic scraper could (especially for bilingual/mixed-language posts).
    If the scraper only had a placeholder ('Unknown' / raw snippet), swap in
    the LLM's version. Company reassignment only happens when the scraper's
    original guess was a placeholder, so we don't clobber a good extraction
    from a source (like AllJobs) that already parses company reliably."""
    row = conn.execute(
        "SELECT j.company_id, c.name AS company_name, j.job_title FROM jobs j "
        "JOIN companies c ON c.id = j.company_id WHERE j.id = ?", (job_id,),
    ).fetchone()
    if not row:
        return
    updates = {}
    if job_title and row["job_title"] != job_title:
        updates["job_title"] = job_title
    new_company_id = None
    if company_name and row["company_name"] in ("Unknown", "", None) and company_name != "Unknown":
        new_company_id = get_or_create_company(conn, company_name)

    if updates or new_company_id:
        sets = []
        params = []
        if "job_title" in updates:
            sets.append("job_title = ?")
            params.append(updates["job_title"])
        if new_company_id:
            sets.append("company_id = ?")
            params.append(new_company_id)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def update_job_enrichment(conn, job_id: int, *, required_tech_stack: list[str], seniority_level: str | None,
                           contact_email: str | None, contact_name: str | None, fit_score: int,
                           language: str | None = None):
    conn.execute(
        """UPDATE jobs SET required_tech_stack = ?, seniority_level = ?, contact_email = ?,
           contact_name = ?, fit_score = ?, language = COALESCE(?, language), updated_at = ?
           WHERE id = ?""",
        (json.dumps(required_tech_stack, ensure_ascii=False), seniority_level, contact_email,
         contact_name, fit_score, language, now_iso(), job_id),
    )


def update_raw_description(conn, job_id: int, raw_description: str):
    conn.execute("UPDATE jobs SET raw_description = ?, updated_at = ? WHERE id = ?",
                 (raw_description, now_iso(), job_id))


def delete_job(conn, job_id: int):
    """Used when a job fails to parse at all (transient error) -- see
    pipeline.py for why genuine relevance-gate rejections are archived
    instead of deleted. Also prunes the company row if it's now orphaned."""
    row = conn.execute("SELECT company_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.execute("DELETE FROM outreach_logs WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    if row:
        remaining = conn.execute("SELECT 1 FROM jobs WHERE company_id = ?", (row["company_id"],)).fetchone()
        if not remaining:
            conn.execute("DELETE FROM companies WHERE id = ?", (row["company_id"],))


def set_job_status(conn, job_id: int, status: str):
    conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), job_id))


def set_outreach_initiated(conn, job_id: int, value: bool):
    conn.execute("UPDATE jobs SET outreach_initiated = ?, updated_at = ? WHERE id = ?",
                 (1 if value else 0, now_iso(), job_id))


def update_company_contact(conn, company_id: int, email: str, confidence: str):
    conn.execute(
        "UPDATE companies SET contact_email = COALESCE(contact_email, ?), contact_email_confidence = COALESCE(contact_email_confidence, ?) WHERE id = ?",
        (email, confidence, company_id),
    )


def insert_outreach_log(conn, *, job_id: int, contact_person: str | None, contact_email: str | None,
                         language: str, email_subject: str, email_body: str) -> int:
    if IS_PG:
        row = conn.execute(
            """INSERT INTO outreach_logs (job_id, contact_person, contact_email, language,
               email_subject, email_body, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?) RETURNING id""",
            (job_id, contact_person, contact_email, language, email_subject, email_body, now_iso()),
        ).fetchone()
        return row["id"]
    cur = conn.execute(
        """INSERT INTO outreach_logs (job_id, contact_person, contact_email, language,
           email_subject, email_body, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?)""",
        (job_id, contact_person, contact_email, language, email_subject, email_body, now_iso()),
    )
    return cur.lastrowid


def fetch_jobs_for_dashboard(conn):
    return conn.execute(
        """SELECT j.id, c.name AS company_name, c.contact_email AS company_email,
                  c.contact_email_confidence, j.job_title, j.job_url, j.source, j.language,
                  j.raw_description, j.posted_date, j.required_tech_stack, j.seniority_level,
                  j.contact_email AS job_contact_email, j.contact_name, j.fit_score, j.status,
                  j.outreach_initiated, j.created_at, j.updated_at
           FROM jobs j JOIN companies c ON c.id = j.company_id
           ORDER BY j.fit_score DESC, j.created_at DESC"""
    ).fetchall()
