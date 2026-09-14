"""Shared types & helpers for every scraper module."""
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import MAX_JOB_AGE_DAYS, ALL_SEARCH_KEYWORDS  # noqa: E402


_RETRY_DELAYS_SEC = [3, 6, 12, 20, 30]  # ~70s total -- a first 3x2s retry (2026-08) wasn't
                                        # enough to ride out whatever transiently locks the
                                        # binary (confirmed: the file is present and launches
                                        # fine moments before/after a failure), so this gives
                                        # it much longer before actually giving up.


def launch_chromium(p, **kwargs):
    """p.chromium.launch(), retried on the intermittent 'Executable doesn't
    exist' error seen locally on this machine even though the file is
    confirmed present and launchable moments before/after the failure --
    most likely Symantec's real-time scanner transiently locking the binary.
    Logs each retry so a persistent failure is visible in logs/dashboard.log
    rather than just looking like one opaque crash."""
    last_err = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS_SEC, start=1):
        if delay:
            time.sleep(delay)
        try:
            return p.chromium.launch(**kwargs)
        except Exception as e:
            last_err = e
            if "Executable doesn't exist" not in str(e):
                raise
            print(f"[launch_chromium] attempt {attempt} failed (Executable doesn't exist), "
                  f"retrying...", flush=True)
    print(f"[launch_chromium] gave up after {len(_RETRY_DELAYS_SEC) + 1} attempts", flush=True)
    raise last_err


@dataclass
class RawJob:
    """What every scraper hands back, before LLM parsing/enrichment."""
    company_name: str
    job_title: str
    job_url: str
    source: str
    raw_description: str = ""
    posted_date: str | None = None  # ISO date string if the source exposes one
    language_hint: str | None = None


def within_recency_window(posted_date: str | None) -> bool:
    """Per user requirement: only keep ads that have been live <= 90 days.
    If a source doesn't expose a posting date, we cannot verify age -- callers
    should treat those as 'unknown, keep for now' rather than silently drop
    real leads, but should log that the recency check couldn't be applied."""
    if not posted_date:
        return True
    try:
        posted = datetime.fromisoformat(posted_date).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - posted) <= timedelta(days=MAX_JOB_AGE_DAYS)


def looks_relevant(title: str, description: str = "") -> bool:
    """Cheap pre-filter so we don't burn LLM calls on obviously unrelated
    postings, while still casting a wide net (per user's 'maximum volume'
    requirement this is intentionally loose -- final judgment is the LLM's
    fit_score, not this filter)."""
    text = f"{title} {description}".lower()
    return any(kw.lower() in text for kw in ALL_SEARCH_KEYWORDS)
