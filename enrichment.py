"""Best-effort company contact discovery.

Honest scope: this module only extracts what's already present in the text
we scraped (an email address printed in the ad, or a company domain
mentioned/linked), plus a low-confidence guessed pattern from that domain.
It does NOT perform live web lookups -- the app has no internet-search
integration of its own, so anything beyond what's in the job post itself
would be a fabricated-looking email, which we deliberately avoid. When
nothing can be extracted, `contact_email_confidence` stays NULL and the
dashboard should let a human paste in a verified address instead.
"""
import re

import tldextract

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")

# Domains that show up in postings but are never the hiring company's own
# site (job boards, ATS vendors, social links, etc.) -- filtered out so we
# don't misattribute e.g. an AllJobs URL as the company's domain.
DOMAIN_DENYLIST = {
    "alljobs.co.il", "drushim.co.il", "jobmaster.co.il", "jobnet.co.il",
    "gotfriends.co.il", "sqlink.com", "dialog.co.il", "secrethunter.co.il",
    "linkedin.com", "indeed.com", "greenhouse.io", "comeet.co",
    "facebook.com", "instagram.com", "wa.me", "whatsapp.com", "google.com",
}


def extract_email(text: str) -> str | None:
    for match in EMAIL_RE.findall(text or ""):
        domain = match.split("@", 1)[1].lower()
        if not any(bad in domain for bad in DOMAIN_DENYLIST):
            return match
    return None


def extract_company_domain(text: str) -> str | None:
    for url in URL_RE.findall(text or ""):
        ext = tldextract.extract(url)
        if not ext.domain or not ext.suffix:
            continue
        registered = f"{ext.domain}.{ext.suffix}"
        if registered not in DOMAIN_DENYLIST:
            return registered
    return None


def enrich_contact(raw_description: str) -> tuple[str | None, str | None]:
    """Returns (email, confidence). confidence is 'confirmed' when an actual
    email was found in the text, 'guessed' when only derived from a domain,
    or None when nothing usable was found."""
    email = extract_email(raw_description)
    if email:
        return email, "confirmed"

    domain = extract_company_domain(raw_description)
    if domain:
        return f"info@{domain}", "guessed"

    return None, None
