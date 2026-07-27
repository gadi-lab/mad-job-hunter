"""Generic RSS ingestion -- e.g. Google Alerts feeds the user sets up for
queries like 'אנליסט נתונים' or '"Data Analyst" site:linkedin.com/jobs'.
This is the sanctioned way to get LinkedIn-adjacent signal without scraping
LinkedIn directly (which we don't do -- see config.SOURCES)."""
import feedparser

from .base import RawJob, looks_relevant


def scrape(feed_urls: list[str]) -> list[RawJob]:
    jobs: list[RawJob] = []
    for url in feed_urls:
        parsed = feedparser.parse(url)
        for entry in parsed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            if not looks_relevant(title, summary):
                continue
            jobs.append(RawJob(
                company_name="Unknown",
                job_title=title,
                job_url=entry.get("link", ""),
                source="rss",
                raw_description=summary,
                posted_date=entry.get("published"),
            ))
    return jobs
