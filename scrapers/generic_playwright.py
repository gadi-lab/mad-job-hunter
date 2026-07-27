"""Generic Playwright-based scraper for smaller Israeli tech-recruitment
boards (JobMaster, Jobnet, Gotfriends, SQLink, Dialog, ...).

These are boutique agencies that deal almost exclusively in tech/BI/data
roles, so instead of guessing each site's internal search query-string
syntax (which we could not fully reverse-engineer for every one of them in
the time available), we pull their *entire* open-positions board and let
`looks_relevant()` + the LLM fit_score do the filtering -- consistent with
the "maximize volume, don't over-filter at scrape time" instruction.

Site entry points are best-effort: we start at a starting URL, and if that
looks like a homepage (no repeated job-card-like links found) we try to
auto-navigate through a nav link containing a jobs-related keyword
(jobs/positions/career/משרות/דרושים). This makes the scraper resilient to
not knowing each site's exact "/jobs" path up front, but selectors should
still be spot-checked against the live site periodically -- boutique-agency
sites redesign without notice far more often than major job boards.
"""
import re

from playwright.sync_api import sync_playwright

from .base import RawJob

JOB_NAV_HINTS = ["job", "position", "vacan", "career", "משרות", "דרושים", "לוח"]
JOB_HREF_HINTS = re.compile(r"job|position|vacan|mishr|drush|career", re.IGNORECASE)
# Sites like Jobnet are two-tier: the homepage only links to *category*
# browse pages (e.g. /jobs?profid=673), not individual postings. This
# pattern flags those so we can drill one level deeper instead of quietly
# returning a list of category labels mislabeled as jobs.
CATEGORY_HREF_RE = re.compile(r"[?&](profid|cat|category|tag|field)=\d+", re.IGNORECASE)
# Broad (not just core-stack) relevance net for deciding which categories to
# drill into -- tech/data-adjacent categories are worth the extra hop even
# if a specific category label doesn't scream "analyst".
CATEGORY_RELEVANCE_HINTS = [
    "data", "אנליסט", "אנליטיקה", "bi", "מחשוב", "הייטק", "טכנולוגיה",
    "תוכנה", "אינטרנט", "דיגיטל", "תוכן", "שיווק", "אבטחת איכות", "qa",
]

SITE_CONFIGS = {
    "jobmaster": {"start_url": "https://www.jobmaster.co.il/jobs/"},
    "jobnet": {"start_url": "https://www.jobnet.co.il/"},
    "gotfriends": {"start_url": "https://www.gotfriends.co.il/"},
    "sqlink": {"start_url": "https://www.sqlink.com/"},
    "dialog": {"start_url": "https://www.dialog.co.il/"},
    "secrethunter": {"start_url": "https://www.secrethunter.co.il/"},
}


def _find_jobs_nav_link(page):
    for a in page.query_selector_all("a"):
        href = (a.get_attribute("href") or "").lower()
        text = (a.inner_text() or "").strip().lower()
        if any(h in href or h in text for h in JOB_NAV_HINTS):
            return a.get_attribute("href")
    return None


def _collect_candidate_links(page, base_url: str):
    candidates = []
    frames = [page] + page.frames
    for frame in frames:
        try:
            anchors = frame.query_selector_all("a")
        except Exception:
            continue
        for a in anchors:
            href = a.get_attribute("href") or ""
            text = (a.inner_text() or "").strip()
            if not href or len(text) < 8:
                continue
            if JOB_HREF_HINTS.search(href) or re.search(r"\d{4,}", href):
                full_href = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                candidates.append((full_href, text, a))
    return candidates


def scrape_site(site_key: str, max_jobs: int = 100) -> list[RawJob]:
    cfg = SITE_CONFIGS[site_key]
    jobs: dict[str, RawJob] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=15000)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        try:
            # networkidle times out on ad-heavy pages that never go quiet;
            # domcontentloaded + a short settle wait is far more reliable
            # across these sites (confirmed: jobnet.co.il never reaches
            # networkidle within any reasonable timeout).
            page.goto(cfg["start_url"], timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
        except Exception:
            browser.close()
            return []

        candidates = _collect_candidate_links(page, cfg["start_url"])
        if len(candidates) < 3:
            nav_href = _find_jobs_nav_link(page)
            if nav_href:
                try:
                    target = nav_href if nav_href.startswith("http") else cfg["start_url"].rstrip("/") + "/" + nav_href.lstrip("/")
                    page.goto(target, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2500)
                    candidates = _collect_candidate_links(page, cfg["start_url"])
                except Exception:
                    pass

        # Two-tier site handling: if everything we found looks like a
        # category browse link rather than an individual posting, drill
        # into the categories that are plausibly tech/data-relevant and
        # re-collect from those pages instead.
        if candidates and all(CATEGORY_HREF_RE.search(href) for href, _, _ in candidates):
            category_links = {href: text for href, text, _ in candidates}
            relevant = [
                href for href, text in category_links.items()
                if any(hint.lower() in text.lower() for hint in CATEGORY_RELEVANCE_HINTS)
            ]
            drilled = []
            for cat_href in relevant[:6]:
                try:
                    page.goto(cat_href, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    drilled += _collect_candidate_links(page, cfg["start_url"])
                except Exception:
                    continue
            candidates = [c for c in drilled if not CATEGORY_HREF_RE.search(c[0])] or candidates

        for href, text, anchor in candidates[:max_jobs]:
            if href in jobs:
                continue
            container = anchor
            try:
                for _ in range(3):
                    parent = container.evaluate_handle("el => el.parentElement")
                    if parent:
                        container = parent.as_element() or container
            except Exception:
                pass
            try:
                snippet = container.inner_text().strip()
            except Exception:
                snippet = text
            jobs[href] = RawJob(
                company_name="Unknown",
                job_title=text,
                job_url=href,
                source=site_key,
                raw_description=snippet[:3000],
            )
        browser.close()
    return list(jobs.values())


def scrape(site_keys: list[str]) -> list[RawJob]:
    all_jobs: list[RawJob] = []
    for key in site_keys:
        if key not in SITE_CONFIGS:
            continue
        try:
            all_jobs.extend(scrape_site(key))
        except Exception:
            continue
    return all_jobs
