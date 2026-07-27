"""Orchestrator: scrape every enabled source -> skip already-seen URLs ->
drop postings older than the 90-day window -> LLM parse & score -> best-
effort contact enrichment -> persist.

Safe to re-run on a schedule (daily cron / Task Scheduler): dedup is by
job_url, so a repeat run only ever adds genuinely new postings -- which is
exactly the "keep appending new jobs to the app" behavior that was asked
for. Run directly:

    python pipeline.py
"""
import json
import sys
import time

import db
import enrichment
import parser as llm_parser
from relevance import passes_hard_filter
from config import (
    ALL_SEARCH_KEYWORDS, SOURCES, GREENHOUSE_BOARD_TOKENS, COMEET_COMPANY_UIDS,
    RSS_FEED_URLS, BASE_DIR,
)
from scrapers.base import RawJob, within_recency_window

SCRAPE_CACHE_PATH = BASE_DIR / "data" / "scrape_cache.json"
LAST_RUN_PATH = BASE_DIR / "data" / "last_run.json"


def _write_last_run(stats: dict):
    from datetime import datetime, timezone
    LAST_RUN_PATH.write_text(
        json.dumps({"finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "stats": stats}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _log(msg: str):
    print(msg, flush=True)


def run_scrapers() -> list[RawJob]:
    raw_jobs: list[RawJob] = []

    if SOURCES["alljobs"]["enabled"]:
        from scrapers import alljobs
        _log("[scrape] alljobs starting...")
        t0 = time.time()
        found = alljobs.scrape(ALL_SEARCH_KEYWORDS)
        raw_jobs += found
        _log(f"[scrape] alljobs done: {len(found)} in {time.time()-t0:.1f}s")

    if SOURCES["drushim"]["enabled"]:
        from scrapers import drushim
        _log("[scrape] drushim starting...")
        t0 = time.time()
        found = drushim.scrape(ALL_SEARCH_KEYWORDS)
        raw_jobs += found
        _log(f"[scrape] drushim done: {len(found)} in {time.time()-t0:.1f}s")

    generic_sites = [k for k in ("jobmaster", "jobnet", "gotfriends", "sqlink", "dialog", "secrethunter")
                      if SOURCES.get(k, {}).get("enabled")]
    if generic_sites:
        from scrapers import generic_playwright
        for site in generic_sites:
            _log(f"[scrape] {site} starting...")
            t0 = time.time()
            found = generic_playwright.scrape_site(site)
            raw_jobs += found
            _log(f"[scrape] {site} done: {len(found)} in {time.time()-t0:.1f}s")

    if SOURCES["greenhouse"]["enabled"]:
        from scrapers import greenhouse
        _log("[scrape] greenhouse...")
        raw_jobs += greenhouse.scrape(GREENHOUSE_BOARD_TOKENS)

    if SOURCES["comeet"]["enabled"]:
        from scrapers import comeet
        _log("[scrape] comeet...")
        raw_jobs += comeet.scrape(COMEET_COMPANY_UIDS)

    if SOURCES["rss"]["enabled"]:
        from scrapers import rss_boards
        _log("[scrape] rss...")
        raw_jobs += rss_boards.scrape(RSS_FEED_URLS)

    return raw_jobs


def _save_scrape_cache(raw_jobs: list[RawJob]):
    data = [
        {"company_name": r.company_name, "job_title": r.job_title, "job_url": r.job_url,
         "source": r.source, "raw_description": r.raw_description, "posted_date": r.posted_date}
        for r in raw_jobs
    ]
    SCRAPE_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_scrape_cache() -> list[RawJob]:
    data = json.loads(SCRAPE_CACHE_PATH.read_text(encoding="utf-8"))
    return [RawJob(**d) for d in data]


def _parse_gate_and_enrich(conn, *, job_id: int, company_id: int, job_title: str, source: str, description: str):
    """Shared by both 'brand new posting' and 'reprocess a pending backlog
    row' paths: LLM parse -> hard relevance gate -> enrichment. Returns one
    of 'parsed' | 'parse_failed' | 'filtered_out'.

    IMPORTANT (cost bug fixed 2026-07): a job that fails the relevance gate
    is a genuine, deterministic verdict on that job's actual content -- re-
    scoring it later would just spend another LLM call to reach the exact
    same answer. It's archived (hidden from the dashboard by the default
    status filter, per the "don't show it at all" requirement) rather than
    deleted, so job_exists() recognizes it next run and never re-pays for
    it. parse_failed is different -- that's a transient API/network error,
    not a verdict on the job, so it stays deleted to allow a real retry."""
    parsed = llm_parser.parse_job_safe(job_title, description, source)
    if not parsed:
        db.delete_job(conn, job_id)
        return "parse_failed"

    tech_stack = parsed.get("required_tech_stack", [])
    final_title = parsed.get("job_title") or job_title
    if not passes_hard_filter(final_title, tech_stack, description):
        db.update_job_enrichment(
            conn, job_id, required_tech_stack=tech_stack,
            seniority_level=parsed.get("seniority_level"), contact_email=None,
            contact_name=None, fit_score=parsed.get("fit_score", 0), language=parsed.get("language"),
        )
        db.set_job_status(conn, job_id, "ARCHIVED")
        return "filtered_out"

    db.finalize_job_core_fields(conn, job_id, company_name=parsed.get("company_name"), job_title=parsed.get("job_title"))
    email, confidence = enrichment.enrich_contact(description)
    if parsed.get("contact_email"):
        email, confidence = parsed["contact_email"], "confirmed"
    db.update_job_enrichment(
        conn, job_id,
        required_tech_stack=tech_stack,
        seniority_level=parsed.get("seniority_level"),
        contact_email=email,
        contact_name=parsed.get("contact_name"),
        fit_score=parsed.get("fit_score", 0),
        language=parsed.get("language"),
    )
    if email:
        db.update_company_contact(conn, company_id, email, confidence)
    return "parsed"


def process_new_job(conn, raw: RawJob):
    if not within_recency_window(raw.posted_date):
        return "skipped_too_old"

    company_id = db.get_or_create_company(conn, raw.company_name)
    job_id = db.insert_job(
        conn,
        company_id=company_id,
        job_title=raw.job_title,
        job_url=raw.job_url,
        source=raw.source,
        raw_description=raw.raw_description,
        posted_date=raw.posted_date,
    )

    # Deliberately NOT fetching the full detail-page description here: that
    # requires a fresh headless-browser launch per short-snippet job, which
    # is both slow and (confirmed in testing) can hang an unattended batch
    # run. The search-result snippet is enough for the LLM to score/gate on;
    # the dashboard fetches the full text lazily, only for the one job a
    # user actually opens -- see description_fetch.py.
    return _parse_gate_and_enrich(
        conn, job_id=job_id, company_id=company_id,
        job_title=raw.job_title, source=raw.source, description=raw.raw_description,
    )


def reprocess_pending(conn) -> dict:
    """One-time-per-backlog catch-up: score/gate any jobs that were
    inserted before an API key was available (fit_score IS NULL), so a
    demo seed done without a key gets properly filtered once a key exists."""
    pending = conn.execute(
        "SELECT id, company_id, job_title, job_url, source, raw_description FROM jobs WHERE fit_score IS NULL"
    ).fetchall()
    stats = {}
    for row in pending:
        result = _parse_gate_and_enrich(
            conn, job_id=row["id"], company_id=row["company_id"],
            job_title=row["job_title"], source=row["source"], description=row["raw_description"] or "",
        )
        stats[result] = stats.get(result, 0) + 1
        # Commit per-job, not once at the end -- this run can take many
        # minutes (each Playwright description fetch + LLM call adds up),
        # and a single multi-minute transaction would both lose all
        # progress on a crash/interrupt and lock out the dashboard/other
        # writers for the entire duration.
        conn.commit()
        print(f"[reprocess] job {row['id']}: {result} ({sum(stats.values())}/{len(pending)})")
        time.sleep(0.2)
    return stats


def parse_new_jobs(raw_jobs: list[RawJob]) -> dict:
    stats = {"new": 0, "duplicate": 0, "skipped_too_old": 0, "parsed": 0, "parse_failed": 0, "filtered_out": 0}
    with db.get_conn() as conn:
        for raw in raw_jobs:
            if not raw.job_url:
                continue
            if db.job_exists(conn, raw.job_url):
                stats["duplicate"] += 1
                continue
            stats["new"] += 1
            result = process_new_job(conn, raw)
            stats[result] = stats.get(result, 0) + 1
            conn.commit()  # per-job commit -- see reprocess_pending for why
            _log(f"[pipeline] {raw.company_name} / {raw.job_title[:50]}: {result}")
            time.sleep(0.2)  # gentle pacing against the LLM API
    return stats


def main():
    """Modes (useful for debugging a slow/stuck phase in isolation):
      python pipeline.py                 -- full run (scrape + parse)
      python pipeline.py --scrape-only    -- scrape all sources, cache to disk, exit
      python pipeline.py --parse-only     -- parse/gate/store from the cached scrape
    """
    db.init_db()
    args = sys.argv[1:]

    if "--parse-only" not in args:
        with db.get_conn() as conn:
            backlog_stats = reprocess_pending(conn)
        if backlog_stats:
            _log(f"[pipeline] reprocessed pending backlog: {backlog_stats}")

    if "--parse-only" in args:
        raw_jobs = _load_scrape_cache()
        _log(f"[pipeline] loaded {len(raw_jobs)} cached raw postings")
    else:
        raw_jobs = run_scrapers()
        _log(f"[pipeline] {len(raw_jobs)} raw postings discovered across all sources")
        _save_scrape_cache(raw_jobs)
        if "--scrape-only" in args:
            _log(f"[pipeline] scrape-only mode: cached to {SCRAPE_CACHE_PATH}, exiting")
            return

    stats = parse_new_jobs(raw_jobs)
    _write_last_run(stats)
    _log(f"[pipeline] done: {stats}")
    return stats


if __name__ == "__main__":
    main()
    sys.exit(0)
