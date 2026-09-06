"""Dead-link sweep: source postings routinely get pulled down (filled,
expired) well before our MAX_JOB_AGE_DAYS window ends, and nothing else in
the pipeline ever re-checks whether a job the dashboard is still showing is
actually still live on the source site. Confirmed directly against Drushim
that a removed posting returns a clean HTTP 410 -- a plain status-code
check is enough, no fragile "removed" text-matching needed."""
import requests

DEAD_STATUS_CODES = {404, 410}


def is_link_dead(url: str, timeout: float = 10.0) -> bool:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        return r.status_code in DEAD_STATUS_CODES
    except requests.RequestException:
        return False  # a network hiccup isn't proof the job is gone -- never archive on a guess


def find_dead_job_ids(conn) -> list[int]:
    """Checks every currently-visible (non-archived) job's URL."""
    rows = conn.execute("SELECT id, job_url FROM jobs WHERE status != 'ARCHIVED'").fetchall()
    return [row["id"] for row in rows if row["job_url"] and is_link_dead(row["job_url"])]
