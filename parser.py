"""Bilingual (Hebrew/English/mixed) LLM extraction + fit-scoring, via the
Claude API's forced tool-use so we always get back valid structured JSON
regardless of what language mix the source posting used."""
import json

import anthropic

from config import ANTHROPIC_API_KEY, PARSE_MODEL, CORE_STACK_TERMS

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # The SDK's default timeout is long (up to 10 min) -- fine for a
        # single interactive call, but during a batch run over hundreds of
        # jobs a single network hiccup can stall the whole pipeline for that
        # long. A tighter timeout + a couple of retries fails fast instead.
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=45.0, max_retries=2)
    return _client


EXTRACT_TOOL = {
    "name": "extract_job_info",
    "description": "Extract structured fields from a job posting that may be written in Hebrew, English, or a mix of both ('Hebrish').",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "The hiring company's name, cleaned up (not a staffing agency name if a real client is named)."},
            "job_title": {"type": "string", "description": "The job title, translated to a clean English title if the source is in Hebrew (keep recognizable Hebrew terms like BI/GA4 as-is)."},
            "language": {"type": "string", "enum": ["he", "en", "mixed"], "description": "Primary language(s) of the original posting."},
            "seniority_level": {"type": "string", "enum": ["Junior", "Mid", "Senior", "Lead", "Unknown"]},
            "required_tech_stack": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The posting's key requirements as short phrases/tags, for a recruiter-facing "
                    "'Requirements' column -- not just tool names. Include specific tools/technologies "
                    "(e.g. GA4, GTM, SQL, BigQuery, Looker Studio, Tableau, Power BI) AND notable "
                    "domain/soft requirements explicitly stated (e.g. 'ידע בשיווק'/'Marketing knowledge', "
                    "'אנגלית ברמה גבוהה'/'Fluent English', 'ניסיון בענף הפיננסים'). Keep each item short "
                    "(2-4 words). Core stack terms to recognize include: " + ", ".join(CORE_STACK_TERMS)
                ),
            },
            "contact_email": {"type": ["string", "null"], "description": "An email address explicitly given in the text for applying, or null."},
            "contact_name": {"type": ["string", "null"], "description": "A named recruiter/HR contact if mentioned, or null."},
            "fit_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": (
                    "How well M:AD's Managed Analyst model fits this role. M:AD embeds a "
                    "trained analyst (GA4, GTM, SQL, BigQuery, Looker Studio, Tableau) inside "
                    "the client 3 days on-site / 2 days remote, backed by senior M:AD data "
                    "leadership. Score high (70-100) when the role IS essentially an in-house "
                    "digital/data/BI analyst position requiring 2+ of the core stack tools. "
                    "Score low (1-30) when the role is unrelated (e.g. general software "
                    "engineering, sales, non-technical) or requires a full-time senior "
                    "leadership hire M:AD doesn't place (e.g. Head of Data, CTO)."
                ),
            },
            "fit_reason": {"type": "string", "description": "One sentence justifying the fit_score."},
        },
        "required": ["company_name", "job_title", "language", "required_tech_stack", "fit_score", "fit_reason"],
    },
}

SYSTEM_PROMPT = """You are a bilingual (Hebrew/English) job-posting analyst for M:AD, \
an Israeli company that trains, places, and manages embedded Digital & Data Analysts \
(GA4, GTM, SQL, BigQuery, Looker Studio, Tableau) under a Managed Analyst model. \
The input job description may be in Hebrew, English, or a mix of both ("Hebrish"). \
Extract the requested fields accurately regardless of language -- do not skip or \
mis-read Hebrew text, and do not assume a posting is irrelevant just because the \
title is in Hebrew. Always call the extract_job_info tool with your answer."""


def parse_job(raw_title: str, raw_description: str, source: str) -> dict:
    client = _get_client()
    user_text = f"Source: {source}\nTitle: {raw_title}\n\nDescription:\n{raw_description[:8000]}"
    resp = client.messages.create(
        model=PARSE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_job_info"},
        messages=[{"role": "user", "content": user_text}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "extract_job_info":
            return block.input
    raise RuntimeError("Claude did not return the expected extract_job_info tool call")


def parse_job_safe(raw_title: str, raw_description: str, source: str) -> dict | None:
    try:
        return parse_job(raw_title, raw_description, source)
    except (anthropic.APIError, RuntimeError, json.JSONDecodeError) as e:
        print(f"[parser] FAILED ({type(e).__name__}): {e}"[:300])
        return None
