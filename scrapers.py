"""
Company blog scrapers for CDR/VCM organisations that don't appear
in mainstream RSS feeds.

Strategy (per source, in order):
  1. Try common RSS paths  (/feed/, /rss.xml, /rss, /blog/feed/, /blog.rss)
  2. Try JSON-LD structured data on the blog/news listing page
  3. Generic <article> / heuristic HTML fallback

LinkedIn scraping is intentionally not supported — it is prohibited by
LinkedIn's ToS and actively blocked.  Google News queries in
news_fetcher.py cover press coverage of all companies listed here.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from news_fetcher import (
    HEADERS,
    LOOKBACK,
    clean_html,
    detect_regions,
    is_partial_paywall,
    is_paywalled,
    make_id,
    parse_date,
    relative_date,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

CDR_SOURCES = [
    # ── Registries & standards ──────────────────────────────────────────────
    {"name": "Verra",             "home": "https://verra.org",                  "blog": "/news/"},
    {"name": "Gold Standard",     "home": "https://www.goldstandard.org",        "blog": "/blog"},
    {"name": "American Carbon Registry", "home": "https://americancarbonregistry.org", "blog": "/news/"},
    # ── CDR marketplaces & platforms ────────────────────────────────────────
    {"name": "Puro.earth",        "home": "https://puro.earth",                 "blog": "/blog"},
    {"name": "CarbonFuture",      "home": "https://www.carbonfuture.earth",      "blog": "/blog"},
    {"name": "CDR.fyi",           "home": "https://www.cdr.fyi",                "blog": "/updates"},
    {"name": "Supercritical",     "home": "https://gosupercritical.com",         "blog": "/blog"},
    # ── CDR technology companies ─────────────────────────────────────────────
    {"name": "Climeworks",        "home": "https://climeworks.com",              "blog": "/news"},
    {"name": "Heirloom Carbon",   "home": "https://www.heirloomcarbon.com",      "blog": "/news"},
    {"name": "Charm Industrial",  "home": "https://charmindustrial.com",         "blog": "/news"},
    {"name": "Running Tide",      "home": "https://www.runningtide.com",         "blog": "/news"},
    {"name": "Lithos Carbon",     "home": "https://www.lithoscarbon.com",        "blog": "/blog"},
    {"name": "CULA",              "home": "https://www.cula.com",               "blog": "/blog"},
    {"name": "Absolute Climate",  "home": "https://www.absoluteclimate.com",     "blog": "/blog"},
    # ── MRV & ratings ───────────────────────────────────────────────────────
    {"name": "Isometric",         "home": "https://isometric.com",              "blog": "/blog"},
    {"name": "BeZero Carbon",     "home": "https://bezerocarbon.com",           "blog": "/insights"},
    {"name": "Sylvera",           "home": "https://www.sylvera.com",            "blog": "/resources/blog"},
    {"name": "Carbon Direct",     "home": "https://www.carbon-direct.com",      "blog": "/insights"},
    # ── Nature-based / blue carbon ───────────────────────────────────────────
    {"name": "Mangrove Systems",  "home": "https://www.mangrove.systems",       "blog": "/blog"},
    {"name": "Pachama",           "home": "https://pachama.com",                "blog": "/blog"},
    {"name": "South Pole",        "home": "https://www.southpole.com",          "blog": "/insights"},
    {"name": "Terrasos",          "home": "https://terrasos.co",                "blog": "/news"},
    # ── Corporate / market infrastructure ───────────────────────────────────
    {"name": "Climate Impact X",  "home": "https://www.climateimpactx.com",     "blog": "/news"},
    {"name": "IETA",              "home": "https://www.ieta.org",               "blog": "/resources/news"},
    {"name": "Ecosystem Marketplace", "home": "https://www.ecosystemmarketplace.com", "blog": "/articles/"},
]

# Common RSS path suffixes to probe in order
RSS_PATHS = [
    "/feed/", "/feed", "/rss.xml", "/rss", "/blog/feed/",
    "/news/feed/", "/blog.rss", "/atom.xml", "/index.xml",
]

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_all_company_blogs() -> list[dict]:
    """Try to fetch articles from every CDR_SOURCES entry."""
    import threading

    all_articles: list[dict] = []
    lock = threading.Lock()

    def fetch_one(src):
        articles = _fetch_source(src)
        with lock:
            all_articles.extend(articles)

    threads = [threading.Thread(target=fetch_one, args=(s,), daemon=True) for s in CDR_SOURCES]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    return all_articles


# ---------------------------------------------------------------------------
# Per-source fetcher
# ---------------------------------------------------------------------------

def _fetch_source(src: dict) -> list[dict]:
    home  = src["home"].rstrip("/")
    topic = "companies"   # all company blog posts go under the Companies tab
    name  = src["name"]

    # 1. Try RSS
    articles = _try_rss(home, name, topic)
    if articles:
        logger.info("RSS OK for %s (%d articles)", name, len(articles))
        return articles

    # 2. Fall back to HTML scraping of the blog/news listing page
    blog_url = home + src.get("blog", "/blog")
    articles = _try_html(blog_url, home, name, topic)
    if articles:
        logger.info("HTML scrape OK for %s (%d articles)", name, len(articles))
    else:
        logger.debug("No articles found for %s", name)
    return articles


# ---------------------------------------------------------------------------
# RSS prober
# ---------------------------------------------------------------------------

def _try_rss(home: str, source: str, topic: str) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - LOOKBACK
    for path in RSS_PATHS:
        url = home + path
        try:
            feed = feedparser.parse(url, request_headers=HEADERS)
            if not feed.entries:
                continue
            results = []
            for entry in feed.entries[:20]:
                a = _entry_to_article(entry, source, topic)
                if a:
                    pub = parse_date(a.get("published") or "")
                    if pub and pub < cutoff:
                        continue
                    results.append(a)
            if results:
                return results
        except Exception:
            continue
    return []


def _entry_to_article(entry, source: str, topic: str) -> dict | None:
    link = getattr(entry, "link", "") or ""
    if not link:
        return None
    title = clean_html(getattr(entry, "title", ""), max_len=200)
    summary = ""
    for field in ("summary", "description"):
        val = getattr(entry, field, None)
        if val:
            candidate = clean_html(val)
            if len(candidate) > 40:
                summary = candidate
                break
    pub = parse_date(getattr(entry, "published", None) or getattr(entry, "updated", None))
    return {
        "id":              make_id(link),
        "title":           title,
        "url":             link,
        "source":          source,
        "published":       pub.isoformat() if pub else None,
        "published_rel":   relative_date(pub),
        "summary":         summary,
        "paywalled":       is_paywalled(link, source),
        "partial_paywall": is_partial_paywall(source),
        "image":           None,
        "topic":           topic,
        "regions":         detect_regions(title, summary, source),
        "via":             "Company Blog (RSS)",
    }


# ---------------------------------------------------------------------------
# HTML scraper
# ---------------------------------------------------------------------------

def _try_html(url: str, home: str, source: str, topic: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.debug("HTML fetch failed %s: %s", url, exc)
        return []

    articles = _extract_json_ld(soup, home, source, topic)
    if articles:
        return articles

    articles = _extract_article_tags(soup, home, url, source, topic)
    if articles:
        return articles

    return _extract_heuristic(soup, home, url, source, topic)


def _extract_json_ld(soup: BeautifulSoup, home: str, source: str, topic: str) -> list[dict]:
    """Parse JSON-LD @type BlogPosting / NewsArticle / Article."""
    cutoff = datetime.now(timezone.utc) - LOOKBACK
    results = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            # Handle @graph
            if item.get("@type") == "WebPage" and "@graph" in item:
                items.extend(item["@graph"])
                continue
            if item.get("@type") not in ("BlogPosting", "NewsArticle", "Article"):
                continue
            link = item.get("url") or item.get("mainEntityOfPage", {})
            if isinstance(link, dict):
                link = link.get("@id", "")
            if not link or not link.startswith("http"):
                continue
            title   = clean_html(item.get("headline") or item.get("name") or "", max_len=200)
            summary = clean_html(item.get("description") or "", max_len=400)
            pub_str = item.get("datePublished") or item.get("dateModified")
            pub     = parse_date(pub_str)
            if pub and pub < cutoff:
                continue
            results.append({
                "id":              make_id(link),
                "title":           title,
                "url":             link,
                "source":          source,
                "published":       pub.isoformat() if pub else None,
                "published_rel":   relative_date(pub),
                "summary":         summary,
                "paywalled":       is_paywalled(link, source),
                "partial_paywall": is_partial_paywall(source),
                "image":           None,
                "topic":           topic,
                "regions":         detect_regions(title, summary, source),
                "via":             "Company Blog",
            })
    return results


def _extract_article_tags(soup: BeautifulSoup, home: str, page_url: str,
                           source: str, topic: str) -> list[dict]:
    """Find <article> elements and extract links + titles."""
    cutoff  = datetime.now(timezone.utc) - LOOKBACK
    results = []
    seen    = set()
    for art in soup.find_all("article")[:25]:
        a_tag = art.find("a", href=True)
        if not a_tag:
            continue
        link = urljoin(page_url, a_tag["href"])
        if link in seen or not _same_origin(link, home):
            continue
        seen.add(link)
        # Title: h1/h2/h3 inside article, or the <a> text
        heading = art.find(["h1", "h2", "h3"])
        title   = clean_html((heading or a_tag).get_text(), max_len=200)
        if not title:
            continue
        # Summary: <p> text
        summary = ""
        for p in art.find_all("p")[:3]:
            t = clean_html(p.get_text(), max_len=400)
            if len(t) > 40:
                summary = t
                break
        # Date: <time> tag or text patterns
        pub = _extract_date(art)
        if pub and pub < cutoff:
            continue
        results.append(_make_article(link, title, summary, pub, source, topic, "Company Blog"))
    return results


def _extract_heuristic(soup: BeautifulSoup, home: str, page_url: str,
                        source: str, topic: str) -> list[dict]:
    """Last resort: find all internal links that look like blog posts."""
    cutoff  = datetime.now(timezone.utc) - LOOKBACK
    results = []
    seen    = set()
    # Post-like URL patterns
    post_re = re.compile(r"/(blog|news|insights?|updates?|articles?|post)/[^/]+/?$", re.I)
    for a_tag in soup.find_all("a", href=True)[:100]:
        link = urljoin(page_url, a_tag["href"])
        if link in seen or not _same_origin(link, home):
            continue
        if not post_re.search(urlparse(link).path):
            continue
        seen.add(link)
        title = clean_html(a_tag.get_text(), max_len=200)
        if len(title) < 10:
            continue
        results.append(_make_article(link, title, "", None, source, topic, "Company Blog"))
        if len(results) >= 10:
            break
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_article(link, title, summary, pub, source, topic, via) -> dict:
    return {
        "id":              make_id(link),
        "title":           title,
        "url":             link,
        "source":          source,
        "published":       pub.isoformat() if pub else None,
        "published_rel":   relative_date(pub),
        "summary":         summary,
        "paywalled":       is_paywalled(link, source),
        "partial_paywall": is_partial_paywall(source),
        "image":           None,
        "topic":           topic,
        "regions":         detect_regions(title, summary, source),
        "via":             via,
    }


def _extract_date(tag) -> datetime | None:
    time_el = tag.find("time")
    if time_el:
        dt = parse_date(time_el.get("datetime") or time_el.get_text())
        if dt:
            return dt
    # Look for date-like text patterns in the element
    text = tag.get_text(" ")
    m = re.search(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
        text, re.I,
    )
    if m:
        return parse_date(m.group(0))
    return None


def _same_origin(url: str, home: str) -> bool:
    try:
        u = urlparse(url)
        h = urlparse(home)
        return u.netloc.lstrip("www.") == h.netloc.lstrip("www.")
    except Exception:
        return False
