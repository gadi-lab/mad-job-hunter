"""Greenhouse public job-board API.

Greenhouse exposes a public, unauthenticated JSON endpoint per company
board -- this is the documented, ToS-friendly way to read a company's open
roles (no scraping/bot-detection concerns). There is no cross-company search
across all Greenhouse boards, so this only covers companies whose board
token you add to GREENHOUSE_BOARD_TOKENS in .env.
"""
import requests

from .base import RawJob, looks_relevant

API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def scrape(board_tokens: list[str]) -> list[RawJob]:
    jobs: list[RawJob] = []
    for token in board_tokens:
        try:
            resp = requests.get(API_URL.format(token=token), timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue
        for posting in data.get("jobs", []):
            title = posting.get("title", "")
            content = posting.get("content", "") or ""
            if not looks_relevant(title, content):
                continue
            jobs.append(RawJob(
                company_name=posting.get("company_name") or token,
                job_title=title,
                job_url=posting.get("absolute_url", ""),
                source="greenhouse",
                raw_description=content,
                posted_date=posting.get("updated_at"),
            ))
    return jobs
