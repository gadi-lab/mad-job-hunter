"""Shared types & helpers for every scraper module."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import MAX_JOB_AGE_DAYS, ALL_SEARCH_KEYWORDS  # noqa: E402


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
