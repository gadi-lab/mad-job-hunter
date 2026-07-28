"""Central configuration: keywords, sources, brand, scoring weights."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "data" / "job_hunter.db"


def _get_secret(key: str, default: str = "") -> str:
    """os.getenv first (works locally via .env, and is how Streamlit Cloud
    is documented to expose flat secrets.toml entries too); falls back to
    st.secrets so a misconfigured deploy fails loudly rather than silently
    running on an empty ephemeral SQLite DB. Guarded because this module is
    also imported by pipeline.py, which has no Streamlit runtime."""
    val = os.getenv(key, "")
    if val:
        return val
    try:
        import streamlit as st
        return str(st.secrets.get(key, default))
    except Exception:
        return default


ANTHROPIC_API_KEY = _get_secret("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = _get_secret("CLAUDE_MODEL", "claude-sonnet-5")
# Bulk per-job scoring (parser.py) runs on every scraped posting, every day --
# this is the cost that actually scales, so it defaults to Anthropic's
# cheapest current model. Structured extraction against a fixed schema plus a
# defined scoring rubric doesn't need Sonnet-level reasoning. Outreach email
# drafting (outreach.py) stays on CLAUDE_MODEL/Sonnet since it only runs on a
# manual click and benefits from the better writing quality.
PARSE_MODEL = _get_secret("PARSE_MODEL", "claude-haiku-4-5-20251001")

GREENHOUSE_BOARD_TOKENS = [t.strip() for t in os.getenv("GREENHOUSE_BOARD_TOKENS", "").split(",") if t.strip()]
COMEET_COMPANY_UIDS = [t.strip() for t in os.getenv("COMEET_COMPANY_UIDS", "").split(",") if t.strip()]
RSS_FEED_URLS = [t.strip() for t in os.getenv("RSS_FEED_URLS", "").split(",") if t.strip()]

# Only ingest/keep postings first seen within this many days (per user requirement).
MAX_JOB_AGE_DAYS = 90

# Bilingual search keywords used across scrapers that support free-text search.
SEARCH_KEYWORDS_EN = [
    "Data Analyst",
    "Web Analyst",
    "Digital Analyst",
    "BI Analyst",
    "GA4 Specialist",
    "Marketing Analyst",
    "Product Analyst",
    "Analytics Engineer",
]

SEARCH_KEYWORDS_HE = [
    "אנליסט נתונים",
    "אנליסט דיגיטל",
    "אנליסט/ית BI",
    "אנליסט עסקי",
    "מנתח/ת נתונים",
    "אנליסט שיווק",
    "מומחה/ית GA4",
]

ALL_SEARCH_KEYWORDS = SEARCH_KEYWORDS_EN + SEARCH_KEYWORDS_HE

# Tech-stack terms used for the fit-score prompt & dashboard filters. Kept
# bilingual/mixed since Israeli job posts freely mix Hebrew and English for
# tool names (tool names themselves are almost always written in Latin script).
CORE_STACK_TERMS = [
    "GA4", "Google Analytics", "GTM", "Google Tag Manager", "Server-Side GTM",
    "SQL", "BigQuery", "BQ", "Dataform", "dbt",
    "Looker Studio", "Looker", "Tableau", "Power BI", "PowerBI",
    "CRO", "E-commerce", "Product Analytics", "Marketing Funnel", "Attribution",
]

# Sources to scan. `enabled` lets you turn a source off without deleting config.
SOURCES = {
    "alljobs": {"enabled": True, "engine": "requests"},
    "drushim": {"enabled": True, "engine": "playwright"},
    "jobmaster": {"enabled": True, "engine": "playwright"},
    "jobnet": {"enabled": True, "engine": "playwright"},
    "gotfriends": {"enabled": True, "engine": "playwright"},
    "sqlink": {"enabled": True, "engine": "playwright"},
    "dialog": {"enabled": True, "engine": "playwright"},
    "secrethunter": {"enabled": False, "engine": "playwright"},  # domain unreachable at setup time — verify URL before enabling
    "greenhouse": {"enabled": bool(GREENHOUSE_BOARD_TOKENS), "engine": "api"},
    "comeet": {"enabled": bool(COMEET_COMPANY_UIDS), "engine": "api"},
    "rss": {"enabled": bool(RSS_FEED_URLS), "engine": "rss"},
    # LinkedIn and Indeed actively block automated scraping (bot-detection /
    # ToS). We do not attempt to bypass that. Left here as documented,
    # disabled extension points for a future official API/partner feed.
    "linkedin": {"enabled": False, "engine": "unsupported"},
    "indeed": {"enabled": False, "engine": "unsupported"},
}

# --- Brand (M:AD) ---------------------------------------------------------
BRAND = {
    "name": "M:AD",
    "tagline": "We are M:AD. Growth by Technology.",
    "website": "www.madgrowth.com",
    "email": "info@madgrowth.com",
    "address": "3 Menashiya St. Mitcham Ha'Tachana, Tel Aviv",
    "color_purple": "#8B3FE8",
    "color_purple_dark": "#5B21B6",
    "color_green": "#2E7D4F",
    "color_mint": "#7FDCB5",
    "color_black": "#111111",
}

STATUS_VALUES = ["NEW", "APPROVED", "CONTACTED", "REPLIED", "REJECTED", "ARCHIVED"]

# --- Hard relevance filter --------------------------------------------------
# Per explicit user requirement (revised 2026-07): this is NOT just a
# scoring signal, it's a hard gate. GA4 is non-negotiable -- a job is only
# kept if GA4(-family) is explicitly required, AND at least one of
# BigQuery/Looker Studio/SQL is also required alongside it. GA4+BigQuery is
# the ideal match (reflected in fit_score), but GA4+Looker or GA4+SQL are
# also acceptable per the user's correction -- title alone is never enough.
GA4_FAMILY_TERMS = ["ga4", "google analytics 4", "google analytics", "gtm", "google tag manager"]
BIGQUERY_FAMILY_TERMS = ["bigquery", "big query", "bq"]
SECONDARY_STACK_TERMS = BIGQUERY_FAMILY_TERMS + ["looker", "looker studio", "sql"]
