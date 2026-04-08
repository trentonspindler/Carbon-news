"""
Carbon Markets News Fetcher
Pulls articles from Google News RSS + direct feeds, detects paywalls, and caches results.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import urllib.parse
import threading
import time
import re
import hashlib
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paywall detection
# ---------------------------------------------------------------------------

PAYWALLED_DOMAINS = {
    "bloomberg.com", "ft.com", "wsj.com", "economist.com",
    "nytimes.com", "thetimes.co.uk", "telegraph.co.uk",
    "argusmedia.com", "icis.com", "woodmackenzie.com",
    "carbonpulse.com", "enverus.com", "businessgreen.com",
    "environmentalleader.com", "axios.com", "spglobal.com",
}

PAYWALLED_SOURCE_KEYWORDS = [
    "bloomberg", "financial times", "wall street journal", "wsj",
    "the economist", "new york times", "carbon pulse",
    "argus media", "argus", "icis", "wood mackenzie", "woodmac",
    "s&p global", "platts", "business green", "environmental leader",
    "axios pro", "the times", "the telegraph", "enverus",
]

# Sources with partial paywalls — flag but still link
PARTIAL_PAYWALL_SOURCES = {"reuters", "nature"}


def is_paywalled(url="", source=""):
    """Return True if the article is likely behind a hard paywall."""
    if source:
        sl = source.lower()
        for kw in PAYWALLED_SOURCE_KEYWORDS:
            if kw in sl:
                return True
    if url:
        try:
            domain = urlparse_domain(url)
            for d in PAYWALLED_DOMAINS:
                if d in domain:
                    return True
        except Exception:
            pass
    return False


def is_partial_paywall(source=""):
    if source:
        sl = source.lower()
        for kw in PARTIAL_PAYWALL_SOURCES:
            if kw in sl:
                return True
    return False


def urlparse_domain(url):
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    return re.sub(r"^www\.", "", domain)


# ---------------------------------------------------------------------------
# Topics and feed configuration
# ---------------------------------------------------------------------------

TOPICS = [
    {
        "id": "vcm",
        "name": "VCM & Carbon Credits",
        "icon": "💱",
        "color": "#16a34a",
        "queries": [
            '"voluntary carbon market"',
            '"carbon credits" VCM trading',
            '"carbon offsets" news market',
            "Verra Gold Standard carbon registry",
            "ICVCM carbon integrity council",
        ],
    },
    {
        "id": "cdr",
        "name": "CDR & Removal Tech",
        "icon": "⚗️",
        "color": "#2563eb",
        "queries": [
            '"direct air capture" carbon',
            '"carbon dioxide removal" CDR technology',
            '"enhanced weathering" carbon removal',
            '"biochar" carbon sequestration credits',
            '"BECCS" bioenergy carbon capture storage',
        ],
    },
    {
        "id": "nature",
        "name": "Nature-Based Solutions",
        "icon": "🌿",
        "color": "#15803d",
        "queries": [
            "REDD+ forest carbon credits",
            '"nature-based solutions" carbon credits',
            '"blue carbon" mangrove seagrass credits',
            '"soil carbon" sequestration credits',
            "avoided deforestation carbon offsets",
        ],
    },
    {
        "id": "policy",
        "name": "Policy & Standards",
        "icon": "⚖️",
        "color": "#92400e",
        "queries": [
            '"Article 6" Paris Agreement carbon trading',
            "CORSIA aviation carbon offsets",
            "VCMI carbon claims framework",
            "SBTi net zero science based targets carbon",
            "carbon market integrity standard",
        ],
    },
    {
        "id": "compliance",
        "name": "Compliance Markets",
        "icon": "📋",
        "color": "#6d28d9",
        "queries": [
            '"EU ETS" carbon allowance price',
            "California cap-and-trade carbon market",
            '"carbon allowances" price ETS',
            "emissions trading scheme ETS news",
        ],
    },
    {
        "id": "corporate",
        "name": "Corporate & Finance",
        "icon": "🏦",
        "color": "#1e40af",
        "queries": [
            "corporate carbon credits purchase net zero",
            "carbon market investment funding",
            '"net zero" carbon offset corporate commitment',
            "carbon finance climate tech deal investment",
        ],
    },
]

DIRECT_FEEDS = [
    {
        "url": "https://www.carbonbrief.org/feed/",
        "source": "Carbon Brief",
        "topic": "vcm",
    },
    {
        "url": "https://insideclimatenews.org/feed/",
        "source": "Inside Climate News",
        "topic": "policy",
    },
    {
        "url": "https://www.theguardian.com/environment/climate-crisis/rss",
        "source": "The Guardian",
        "topic": "vcm",
    },
    {
        "url": "https://e360.yale.edu/feed",
        "source": "Yale E360",
        "topic": "nature",
    },
    {
        "url": "https://www.climatechangenews.com/feed/",
        "source": "Climate Home News",
        "topic": "policy",
    },
    {
        "url": "https://carbon180.org/feed/",
        "source": "Carbon180",
        "topic": "cdr",
    },
    {
        "url": "https://sdg.iisd.org/news/feed/",
        "source": "IISD SDG",
        "topic": "policy",
    },
]

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict = {}
_cache_time: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 1800  # 30 minutes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CarbonMarketsDaily/1.0; "
        "+https://github.com/carbon-markets-daily)"
    )
}


def clean_html(text: str, max_len: int = 400) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    clean = soup.get_text(separator=" ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_len]


def parse_date(date_str) -> datetime | None:
    if not date_str:
        return None
    # Try RFC 2822 (standard RSS format)
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    # Fall back to dateutil
    try:
        from dateutil import parser as dp
        dt = dp.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    return None


def relative_date(dt: datetime | None) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    delta = now - dt
    hours = int(delta.total_seconds() / 3600)
    if hours < 1:
        mins = int(delta.total_seconds() / 60)
        return f"{max(mins, 1)}m ago"
    if hours < 24:
        return f"{hours}h ago"
    days = delta.days
    if days == 1:
        return "Yesterday"
    return f"{days}d ago"


def make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

ONE_WEEK = timedelta(days=7)


def fetch_google_news(query: str, max_results: int = 10) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        cutoff = datetime.now(timezone.utc) - ONE_WEEK
        articles = []

        for entry in feed.entries[:max_results]:
            try:
                pub = parse_date(getattr(entry, "published", None))
                if pub and pub < cutoff:
                    continue

                link = getattr(entry, "link", "") or ""
                if not link:
                    continue

                source = ""
                if hasattr(entry, "source") and entry.source:
                    source = entry.source.get("title", "")

                # Strip " - Source Name" suffix Google News appends to titles
                title = clean_html(getattr(entry, "title", ""), max_len=200)
                if source and title.endswith(f" - {source}"):
                    title = title[: -len(f" - {source}")]

                summary = clean_html(getattr(entry, "summary", ""))

                articles.append(
                    {
                        "id": make_id(link),
                        "title": title,
                        "url": link,
                        "source": source,
                        "published": pub.isoformat() if pub else None,
                        "published_rel": relative_date(pub),
                        "summary": summary,
                        "paywalled": is_paywalled(link, source),
                        "partial_paywall": is_partial_paywall(source),
                        "image": None,
                        "via": "Google News",
                    }
                )
            except Exception as exc:
                logger.debug("Entry parse error: %s", exc)

        return articles
    except Exception as exc:
        logger.warning("Google News fetch failed for %r: %s", query, exc)
        return []


def fetch_direct_feed(feed_info: dict) -> list[dict]:
    try:
        feed = feedparser.parse(feed_info["url"], request_headers=HEADERS)
        cutoff = datetime.now(timezone.utc) - ONE_WEEK
        articles = []

        for entry in feed.entries[:25]:
            try:
                pub = parse_date(
                    getattr(entry, "published", None)
                    or getattr(entry, "updated", None)
                )
                if pub and pub < cutoff:
                    continue

                link = getattr(entry, "link", "") or ""
                if not link:
                    continue

                title = clean_html(getattr(entry, "title", ""), max_len=200)

                # Best summary available
                summary = ""
                for field in ("summary", "description"):
                    val = getattr(entry, field, None)
                    if val:
                        candidate = clean_html(val)
                        if len(candidate) > 50:
                            summary = candidate
                            break
                if not summary and hasattr(entry, "content") and entry.content:
                    summary = clean_html(entry.content[0].get("value", ""))

                # Image
                image = None
                if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                    image = entry.media_thumbnail[0].get("url")
                elif hasattr(entry, "media_content") and entry.media_content:
                    for m in entry.media_content:
                        if "image" in m.get("type", "") or m.get("medium") == "image":
                            image = m.get("url")
                            break

                source = feed_info["source"]
                articles.append(
                    {
                        "id": make_id(link),
                        "title": title,
                        "url": link,
                        "source": source,
                        "published": pub.isoformat() if pub else None,
                        "published_rel": relative_date(pub),
                        "summary": summary,
                        "paywalled": is_paywalled(link, source),
                        "partial_paywall": is_partial_paywall(source),
                        "image": image,
                        "topic": feed_info["topic"],
                        "via": "Direct Feed",
                    }
                )
            except Exception as exc:
                logger.debug("Feed entry error: %s", exc)

        return articles
    except Exception as exc:
        logger.warning("Direct feed failed %s: %s", feed_info["url"], exc)
        return []


# ---------------------------------------------------------------------------
# Meta scraper (lightweight — only fetches <head> bytes for OG description)
# ---------------------------------------------------------------------------

def fetch_og_description(url: str, timeout: int = 5) -> str | None:
    """Stream just enough bytes to grab the <head> block for OG meta tags."""
    # Skip known paywalled domains — don't even try
    try:
        domain = urlparse_domain(url)
        if any(d in domain for d in PAYWALLED_DOMAINS):
            return None
    except Exception:
        pass

    try:
        with requests.get(
            url, headers=HEADERS, stream=True, timeout=timeout, allow_redirects=True
        ) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type:
                return None
            # Read at most 12 KB — enough for <head>
            chunk = b""
            for data in resp.iter_content(chunk_size=4096):
                chunk += data
                if len(chunk) >= 12288:
                    break
        soup = BeautifulSoup(chunk, "html.parser")
        for name in ("og:description", "description", "twitter:description"):
            tag = soup.find("meta", attrs={"name": name}) or soup.find(
                "meta", attrs={"property": name}
            )
            if tag and tag.get("content"):
                return clean_html(tag["content"], max_len=350)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main aggregator
# ---------------------------------------------------------------------------

def get_articles(force_refresh: bool = False) -> list[dict]:
    """Return aggregated, deduplicated, sorted articles (cached 30 min)."""
    key = "all_articles"
    now = time.time()

    with _cache_lock:
        if not force_refresh and key in _cache:
            if now - _cache_time.get(key, 0) < CACHE_TTL:
                return _cache[key]

    all_articles: list[dict] = []
    seen_urls: set[str] = set()
    result_lock = threading.Lock()

    def add(articles: list[dict], topic_id: str | None = None):
        with result_lock:
            for a in articles:
                url = a.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    if topic_id and "topic" not in a:
                        a["topic"] = topic_id
                    all_articles.append(a)

    threads = []
    sem = threading.Semaphore(6)  # cap concurrent outbound requests

    def guarded_gn(query, topic_id):
        with sem:
            add(fetch_google_news(query), topic_id)

    def guarded_df(feed_info):
        with sem:
            add(fetch_direct_feed(feed_info), feed_info.get("topic"))

    for feed_info in DIRECT_FEEDS:
        t = threading.Thread(target=guarded_df, args=(feed_info,), daemon=True)
        threads.append(t)
        t.start()

    for topic in TOPICS:
        for query in topic["queries"][:2]:  # 2 queries per topic to stay polite
            t = threading.Thread(
                target=guarded_gn, args=(query, topic["id"]), daemon=True
            )
            threads.append(t)
            t.start()

    for t in threads:
        t.join(timeout=30)

    # Enrich short-summary paywalled articles with OG description (async, best-effort)
    enrich_threads = []
    for article in all_articles:
        if article.get("paywalled") and len(article.get("summary", "")) < 80:
            t = threading.Thread(
                target=_enrich_summary, args=(article,), daemon=True
            )
            enrich_threads.append(t)
            t.start()
    for t in enrich_threads:
        t.join(timeout=10)

    # Sort newest first; undated articles go to the bottom
    def sort_key(a):
        p = a.get("published")
        if p:
            try:
                dt = datetime.fromisoformat(p)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)

    all_articles.sort(key=sort_key, reverse=True)

    with _cache_lock:
        _cache[key] = all_articles
        _cache_time[key] = now

    return all_articles


def _enrich_summary(article: dict):
    desc = fetch_og_description(article["url"])
    if desc and len(desc) > len(article.get("summary", "")):
        article["summary"] = desc


def get_cache_age_minutes() -> int | None:
    ts = _cache_time.get("all_articles")
    if ts is None:
        return None
    return int((time.time() - ts) / 60)
