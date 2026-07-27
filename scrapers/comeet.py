"""Comeet public careers-API.

Like Greenhouse, Comeet exposes a public per-company JSON feed of open
positions. This is the legitimate, ToS-friendly integration point (no login,
no bot-detection bypass). Requires each company's Comeet UID in
COMEET_COMPANY_UIDS (.env) since there's no cross-company search.
"""
import requests

from .base import RawJob, looks_relevant

API_URL = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions"


def scrape(company_uids: list[str]) -> list[RawJob]:
    jobs: list[RawJob] = []
    for uid in company_uids:
        try:
            resp = requests.get(API_URL.format(uid=uid), timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue
        positions = data if isinstance(data, list) else data.get("positions", [])
        for posting in positions:
            name = posting.get("name", "")
            description = " ".join(
                section.get("value", "") for section in posting.get("details", [])
                if isinstance(section, dict)
            )
            if not looks_relevant(name, description):
                continue
            urls = posting.get("urls", {})
            job_url = urls.get("careers_page") if isinstance(urls, dict) else None
            jobs.append(RawJob(
                company_name=posting.get("company_name") or uid,
                job_title=name,
                job_url=job_url or f"https://www.comeet.co/jobs/{uid}/{posting.get('uid', '')}",
                source="comeet",
                raw_description=description,
            ))
    return jobs
