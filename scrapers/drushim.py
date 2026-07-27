"""Drushim scraper.

Drushim is a Vue/Nuxt SPA -- listings only appear after client-side render,
so this uses Playwright (headless Chromium) rather than plain requests.
Verified structure (2026-07):

  <div class="job-item" data-cy="job-item0">
    <div listingid="29861389" class="job-item-main ...">
      <h3><span class="job-url">{TITLE}</span></h3>
      ...
  <a href="https://www.drushim.co.il/job/{id}/{hash}/">  <- detail link

Company name isn't reliably in a single stable class on the card, so we
grab the full card text as raw_description and let the LLM parser (which
handles Hebrew/English/mixed text) extract company_name authoritatively --
see db.finalize_job_core_fields.
"""
import re
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from .base import RawJob

BASE_URL = "https://www.drushim.co.il"


def _search_url(keyword: str) -> str:
    slug = keyword.strip().replace(" ", "-")
    return f"{BASE_URL}/jobs/search/{quote(slug)}/"


def scrape(keywords: list[str], max_per_keyword: int = 40) -> list[RawJob]:
    jobs: dict[str, RawJob] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=15000)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        for kw in keywords:
            try:
                page.goto(_search_url(kw), timeout=20000, wait_until="domcontentloaded")
                page.wait_for_selector(".job-item", timeout=10000)
            except Exception:
                continue

            cards = page.query_selector_all(".job-item")
            for card in cards[:max_per_keyword]:
                link_el = card.query_selector('a[href*="/job/"]')
                if not link_el:
                    continue
                href = link_el.get_attribute("href") or ""
                if href.startswith("/"):
                    href = BASE_URL + href
                if not re.search(r"/job/\d+/", href):
                    continue
                title_el = card.query_selector(".job-url, h3")
                title = title_el.inner_text().strip() if title_el else kw
                full_text = card.inner_text().strip()
                # Verified card text layout is consistently
                # "{title}\n\n{company}\n\n{location/exp/type}\n\n...", so we
                # can read the company name directly rather than relying
                # solely on the LLM fallback in db.finalize_job_core_fields.
                lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]
                company = lines[1] if len(lines) > 1 else "Unknown"
                jobs[href] = RawJob(
                    company_name=company,
                    job_title=title or kw,
                    job_url=href,
                    source="drushim",
                    raw_description=full_text,
                )
        browser.close()
    return list(jobs.values())


def fetch_full_description(job_url: str) -> str:
    """The search-card text is a short snippet; the detail page has the
    real job description. Verified content selector (2026-07): `.jobDes`
    holds just the description+requirements text, no site chrome. Falling
    back to `body` (previous behavior) pulled in the entire page -- nav,
    category lists, footer, cookie banner -- which is what showed up
    looking broken in the dashboard."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, timeout=15000)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page.goto(job_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            content = page.query_selector(".jobDes, .job-details-box, .job-details")
            text = content.inner_text().strip() if content else ""
            browser.close()
            return text[:6000]
    except Exception:
        return ""
