"""AllJobs scraper.

AllJobs server-renders its search results (confirmed via direct HTTP fetch),
so a lightweight requests+BeautifulSoup approach works without a headless
browser. Verified card structure (2026-07):

  <div id="job-box-container{ID}">
    ...
    <div class="job-content-top-date">לפני דקה</div>       <- relative Hebrew time
    <div class="job-content-top-title ">
      <a href="/Search/UploadSingle.aspx?JobID={ID}"><h2>{TITLE}</h2></a>
      <div class="T14"><a href="/Employer/HP/Default.aspx?cid={CID}">{COMPANY}</a></div>
    </div>

AllJobs' `freetext` query param does not reliably restrict server-side
results to the keyword (it mixes in generic "hot board" listings), so we
keep the cheap `looks_relevant()` pre-filter from scrapers.base to cut
obvious noise before it reaches the LLM -- per the user's instruction this
stays deliberately loose; the LLM fit_score is the real filter.
"""
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from .base import RawJob, looks_relevant

BASE_URL = "https://www.alljobs.co.il"
SEARCH_URL = f"{BASE_URL}/SearchResultsGuest.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_RELATIVE_RE = re.compile(
    r"לפני\s+(\d+)?\s*(דקה|דקות|שעה|שעות|יום|ימים|שבוע|שבועות|חודש|חודשים)"
)
_UNIT_TO_TIMEDELTA = {
    "דקה": lambda n: timedelta(minutes=n or 1),
    "דקות": lambda n: timedelta(minutes=n or 1),
    "שעה": lambda n: timedelta(hours=n or 1),
    "שעות": lambda n: timedelta(hours=n or 1),
    "יום": lambda n: timedelta(days=n or 1),
    "ימים": lambda n: timedelta(days=n or 1),
    "שבוע": lambda n: timedelta(weeks=n or 1),
    "שבועות": lambda n: timedelta(weeks=n or 1),
    "חודש": lambda n: timedelta(days=30 * (n or 1)),
    "חודשים": lambda n: timedelta(days=30 * (n or 1)),
}


def parse_relative_hebrew_date(text: str) -> str | None:
    """'לפני 3 ימים' -> ISO date ~3 days ago. Returns None if unparseable."""
    if not text:
        return None
    m = _RELATIVE_RE.search(text.strip())
    if not m:
        return None
    count = int(m.group(1)) if m.group(1) else None
    unit = m.group(2)
    delta = _UNIT_TO_TIMEDELTA[unit](count)
    return (datetime.now(timezone.utc) - delta).isoformat()


def fetch_search_page(keyword: str, page: int = 1) -> str:
    # NOTE: the query param is `freetxt`, not the more intuitive `freetext`
    # -- confirmed by driving AllJobs' own search box in a browser and
    # reading back the URL it produced. Getting this wrong silently returns
    # an unfiltered "hot board" listing instead of erroring, so it's an easy
    # bug to miss (that's exactly what happened during initial testing).
    resp = requests.get(
        SEARCH_URL,
        params={"page": page, "position": "", "freetxt": keyword, "type": "", "region": ""},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


def parse_jobs(html: str, keyword: str) -> list[RawJob]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[RawJob] = []
    for card in soup.select('div[id^="job-box-container"]'):
        title_a = card.select_one(".job-content-top-title a")
        company_a = card.select_one(".job-content-top-title .T14 a")
        date_div = card.select_one(".job-content-top-date")
        if not title_a or not title_a.get("href"):
            continue
        title = title_a.get_text(strip=True)
        company = company_a.get_text(strip=True) if company_a else "Unknown"
        job_url = BASE_URL + title_a["href"] if title_a["href"].startswith("/") else title_a["href"]
        posted_date = parse_relative_hebrew_date(date_div.get_text(strip=True) if date_div else "")

        # AllJobs' `freetext` param doesn't reliably restrict server-side
        # results (it mixes in unrelated "hot board" listings regardless of
        # query) -- so we must actually check title relevance ourselves,
        # against the title alone (never against the search keyword itself,
        # which would trivially "match" every card and filter nothing).
        if not (keyword.lower() in title.lower() or looks_relevant(title)):
            continue

        jobs.append(RawJob(
            company_name=company,
            job_title=title,
            job_url=job_url,
            source="alljobs",
            posted_date=posted_date,
        ))
    return jobs


def fetch_full_description(job_url: str) -> str:
    """Best-effort fetch of the full job detail page text for LLM parsing.

    Verified selector (2026-07): the real per-posting id is
    `job-body-content{JobID}` (an exact-id selector never matched, silently
    falling through to whole-page text -- nav/footer/cookie-banner and
    all -- which is what showed up looking broken in the dashboard)."""
    try:
        resp = requests.get(job_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        content = soup.select_one('[id^="job-body-content"], .job-content-body, .job-full-description')
        if content:
            return content.get_text("\n", strip=True)
        return soup.get_text("\n", strip=True)[:5000]
    except requests.RequestException:
        return ""


def scrape(keywords: list[str]) -> list[RawJob]:
    all_jobs: dict[str, RawJob] = {}
    for kw in keywords:
        try:
            html = fetch_search_page(kw)
            for job in parse_jobs(html, kw):
                all_jobs[job.job_url] = job  # dedup within this run by URL
        except requests.RequestException:
            continue
    return list(all_jobs.values())
