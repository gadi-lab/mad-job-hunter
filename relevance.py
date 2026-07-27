"""Hard post-parse relevance gate (see config.py for the why). Applied
after LLM extraction, using the LLM's cleaned-up required_tech_stack so it
works the same whether the source posting was Hebrew, English, or mixed --
unlike a raw-text keyword search, the LLM has already normalized tool names
like 'BQ' -> 'BigQuery', 'GA' -> 'GA4', etc.

Rule (revised 2026-07, per explicit user correction): title alone is never
sufficient. GA4(-family) must actually be required, AND at least one of
BigQuery/Looker Studio/SQL must also be required. GA4+BigQuery is the ideal
match (see parser.py's fit_score), but GA4+Looker or GA4+SQL also pass."""
from config import GA4_FAMILY_TERMS, SECONDARY_STACK_TERMS


def _contains_any(haystack: str, terms: list[str]) -> bool:
    haystack = haystack.lower()
    return any(term in haystack for term in terms)


def passes_hard_filter(job_title: str, required_tech_stack: list[str], raw_description: str = "") -> bool:
    stack_text = " ".join(required_tech_stack or []).lower()
    combined = f"{stack_text} {raw_description or ''}".lower()

    has_ga4 = _contains_any(combined, GA4_FAMILY_TERMS)
    has_secondary = _contains_any(combined, SECONDARY_STACK_TERMS)
    return has_ga4 and has_secondary
