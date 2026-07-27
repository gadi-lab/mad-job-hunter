"""On-demand (lazy) full job-description fetching, used by the dashboard
when a user opens a specific posting whose scraped snippet is short.

This used to run eagerly for every short-snippet job during the bulk
pipeline run -- that meant launching a fresh headless browser per job,
which was both slow (minutes added per run) and, confirmed in testing, can
hang a long unattended batch. Fetching lazily for just the one job a human
is actually looking at avoids both problems.
"""
from config import BASE_DIR  # noqa: F401 (kept for path-relative future use)


def fetch_full_description(source: str, job_url: str) -> str:
    try:
        if source == "alljobs":
            from scrapers import alljobs
            return alljobs.fetch_full_description(job_url)
        if source == "drushim":
            from scrapers import drushim
            return drushim.fetch_full_description(job_url)
    except Exception:
        pass
    return ""
