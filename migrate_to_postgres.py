"""One-time migration: copy all data from the local SQLite file into the
Postgres database configured via DATABASE_URL. Run once, after Postgres is
set up, so the switch to a shared DB doesn't lose today's already-scored
postings (and doesn't re-spend API calls re-scoring them).

Uses batched inserts (psycopg2.extras.execute_values) rather than one
round-trip per row -- individual INSERTs were hitting intermittent
statement timeouts against the pooler (transient, not data-related: the
same rows succeeded when retried standalone), and batching sidesteps that
by cutting ~900 round trips down to a handful."""
import sqlite3

import psycopg2.extras

import db
from config import DB_PATH

assert db.IS_PG, "DATABASE_URL must be set (pointing at Postgres) to run this migration"

src = sqlite3.connect(DB_PATH)
src.row_factory = sqlite3.Row

BATCH = 50

with db.get_conn() as pg:
    raw_conn = pg._conn
    cur = raw_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    companies = src.execute("SELECT * FROM companies").fetchall()
    company_id_map = {}
    for start in range(0, len(companies), BATCH):
        chunk = companies[start:start + BATCH]
        rows = psycopg2.extras.execute_values(
            cur,
            """INSERT INTO companies (name, domain, contact_email, contact_email_confidence, industry, created_at)
               VALUES %s RETURNING id""",
            [(c["name"], c["domain"], c["contact_email"], c["contact_email_confidence"], c["industry"], c["created_at"]) for c in chunk],
            fetch=True,
        )
        for c, row in zip(chunk, rows):
            company_id_map[c["id"]] = row["id"]
        raw_conn.commit()
        print(f"companies: {min(start+BATCH, len(companies))}/{len(companies)}", flush=True)
    print(f"migrated {len(companies)} companies")

    jobs = src.execute("SELECT * FROM jobs").fetchall()
    job_id_map = {}
    for start in range(0, len(jobs), BATCH):
        chunk = jobs[start:start + BATCH]
        rows = psycopg2.extras.execute_values(
            cur,
            """INSERT INTO jobs (company_id, job_title, job_url, source, language, raw_description,
               posted_date, required_tech_stack, seniority_level, contact_email, contact_name,
               fit_score, status, outreach_initiated, created_at, updated_at)
               VALUES %s RETURNING id""",
            [(company_id_map[j["company_id"]], j["job_title"], j["job_url"], j["source"], j["language"],
              j["raw_description"], j["posted_date"], j["required_tech_stack"], j["seniority_level"],
              j["contact_email"], j["contact_name"], j["fit_score"], j["status"], j["outreach_initiated"],
              j["created_at"], j["updated_at"]) for j in chunk],
            fetch=True,
        )
        for j, row in zip(chunk, rows):
            job_id_map[j["id"]] = row["id"]
        raw_conn.commit()
        print(f"jobs: {min(start+BATCH, len(jobs))}/{len(jobs)}", flush=True)
    print(f"migrated {len(jobs)} jobs")

    logs = src.execute("SELECT * FROM outreach_logs").fetchall()
    log_rows = [
        (job_id_map[log["job_id"]], log["contact_person"], log["contact_email"], log["language"],
         log["email_subject"], log["email_body"], log["status"], log["created_at"], log["sent_at"])
        for log in logs if log["job_id"] in job_id_map
    ]
    if log_rows:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO outreach_logs (job_id, contact_person, contact_email, language,
               email_subject, email_body, status, created_at, sent_at) VALUES %s""",
            log_rows,
        )
        raw_conn.commit()
    print(f"migrated {len(log_rows)} outreach logs")

    cur.close()

src.close()
print("DONE")
