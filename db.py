"""
SQLite persistence for Carbon Markets Daily.

Articles are saved on every fetch cycle so history accumulates over time.
Past weeks are served from the DB; the current week uses the live RSS cache.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

# Allow override via environment variable (useful for Railway persistent volumes)
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "articles.db"),
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                url             TEXT UNIQUE NOT NULL,
                source          TEXT,
                published       TEXT,
                summary         TEXT,
                paywalled       INTEGER DEFAULT 0,
                partial_paywall INTEGER DEFAULT 0,
                image           TEXT,
                topic           TEXT,
                regions         TEXT DEFAULT '[]',
                via             TEXT,
                fetched_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_pub   ON articles(published)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_topic ON articles(topic)")
        c.commit()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def save_articles(articles: list[dict]):
    """Upsert a batch of articles. Silently skips records missing id/url."""
    with _conn() as c:
        for a in articles:
            if not a.get("id") or not a.get("url"):
                continue
            c.execute(
                """
                INSERT OR REPLACE INTO articles
                  (id, title, url, source, published, summary,
                   paywalled, partial_paywall, image, topic, regions, via)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    a["id"],
                    a.get("title", ""),
                    a["url"],
                    a.get("source"),
                    a.get("published"),
                    a.get("summary"),
                    int(bool(a.get("paywalled"))),
                    int(bool(a.get("partial_paywall"))),
                    a.get("image"),
                    a.get("topic"),
                    json.dumps(a.get("regions") or []),
                    a.get("via"),
                ),
            )
        c.commit()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_articles_for_week(
    offset: int = 0,
) -> tuple[list[dict], datetime, datetime]:
    """
    Return (articles, week_start, week_end) for the calendar week at `offset`.
    offset=0  → current week (Mon–Sun containing today)
    offset=-1 → last week, etc.
    """
    start, end = _week_bounds(offset)
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM articles
            WHERE published >= ? AND published < ?
            ORDER BY published DESC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_hydrate(r) for r in rows], start, end


def get_available_weeks(max_weeks: int = 8) -> list[dict]:
    """
    Return metadata for up to max_weeks calendar weeks (current + past).
    Always includes offset=0 even if empty (current week shows live data).
    """
    result = []
    for offset in range(0, -max_weeks, -1):
        start, end = _week_bounds(offset)
        with _conn() as c:
            count = c.execute(
                "SELECT COUNT(*) FROM articles WHERE published >= ? AND published < ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()[0]
        result.append(
            {
                "offset": offset,
                "week_start": start.isoformat(),
                "week_end": end.isoformat(),
                "count": count,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _week_bounds(offset: int) -> tuple[datetime, datetime]:
    """Monday 00:00 UTC → Sunday 23:59 UTC for the week at offset."""
    now   = datetime.now(timezone.utc)
    mon   = now - timedelta(days=now.weekday())
    start = mon.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start + timedelta(weeks=offset)
    end   = start + timedelta(days=7)
    return start, end


def _hydrate(row) -> dict:
    d                   = dict(row)
    d["paywalled"]       = bool(d.get("paywalled"))
    d["partial_paywall"] = bool(d.get("partial_paywall"))
    d["regions"]         = json.loads(d.get("regions") or "[]")
    # Recalculate human-readable relative date
    if d.get("published"):
        try:
            from dateutil import parser as dp
            from news_fetcher import relative_date
            pub = dp.parse(d["published"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            d["published_rel"] = relative_date(pub)
        except Exception:
            d["published_rel"] = ""
    return d
