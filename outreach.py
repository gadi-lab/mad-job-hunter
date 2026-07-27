"""Cold-outreach email drafting. This module only ever produces DRAFT text
for a human to review and send themselves -- nothing here sends email."""
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, BRAND

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=45.0, max_retries=2)
    return _client


DRAFT_TOOL = {
    "name": "draft_outreach_email",
    "description": "Draft a B2B cold outreach email subject + body.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["subject", "body"],
    },
}

SYSTEM_PROMPT = f"""You write short, sharp B2B cold outreach emails for {BRAND['name']} \
({BRAND['tagline']}), an Israeli company that trains, places, and manages embedded \
Digital & Data Analysts (GA4, GTM, SQL, BigQuery, Looker Studio, Tableau).

The recipient is a hiring company that just posted a job ad for an in-house digital/\
data/BI analyst. Your job is to reframe their open req as something {BRAND['name']} can solve \
today: a ready-to-go, pre-trained analyst working 3 days on-site / 2 days remote, \
backed by {BRAND['name']}'s senior data leadership for quality control -- with zero \
hiring, onboarding, or turnover risk (if the analyst ever leaves, {BRAND['name']} \
replaces them).

Rules:
- Write in the SAME language as the job posting (Hebrew if the posting was Hebrew, \
English if English). If mixed, write in Hebrew with the tool/tech names in Latin script \
as they'd naturally appear in Israeli business Hebrew.
- Reference the specific job title and 1-2 specific tech-stack items from the posting \
so it's obviously not a generic template.
- Keep it short (under 130 words in the body), no fluff, one clear call to action \
(a short call this week).
- Sign off with {BRAND['name']}, {BRAND['website']}, {BRAND['email']}.
- Never invent facts about the recipient company you weren't given.
- Always call the draft_outreach_email tool with your answer.
"""


def draft_email(*, company_name: str, job_title: str, tech_stack: list[str], language: str) -> dict:
    client = _get_client()
    lang_label = {"he": "Hebrew", "en": "English", "mixed": "Hebrew (mixed-language source)"}.get(language, "Hebrew")
    user_text = (
        f"Company: {company_name}\n"
        f"Job title: {job_title}\n"
        f"Tech stack mentioned: {', '.join(tech_stack) if tech_stack else 'not specified'}\n"
        f"Write this email in: {lang_label}"
    )
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "draft_outreach_email"},
        messages=[{"role": "user", "content": user_text}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "draft_outreach_email":
            return block.input
    raise RuntimeError("Claude did not return the expected draft_outreach_email tool call")
