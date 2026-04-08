"""
Carbon Markets Daily — Flask application
"""

import logging
import threading

from flask import Flask, jsonify, render_template, request

from db import get_articles_for_week, get_available_weeks, init_db
from news_fetcher import REGIONS, TOPICS, get_articles, get_cache_age_minutes

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = Flask(__name__)
init_db()

_refresh_lock = threading.Lock()
_refreshing = False


@app.route("/")
def index():
    return render_template("index.html", topics=TOPICS, regions=REGIONS)


@app.route("/api/articles")
def articles():
    topic            = request.args.get("topic",    "all").strip()
    region           = request.args.get("region",   "all").strip()
    search           = request.args.get("search",   "").lower().strip()
    paywalled_filter = request.args.get("paywalled","all")
    week_offset      = int(request.args.get("week", "0"))

    # Week 0 = live cache; past weeks come from the DB
    if week_offset == 0:
        data = get_articles()
        week_start = week_end = None
        cache_age  = get_cache_age_minutes()
    else:
        data, week_start, week_end = get_articles_for_week(week_offset)
        cache_age = None  # DB data, not cached

    if topic != "all":
        data = [a for a in data if a.get("topic") == topic]

    if region != "all":
        data = [a for a in data if region in (a.get("regions") or [])]

    if search:
        data = [
            a for a in data
            if search in (a.get("title")   or "").lower()
            or search in (a.get("summary") or "").lower()
            or search in (a.get("source")  or "").lower()
        ]

    if paywalled_filter == "free":
        data = [a for a in data if not a.get("paywalled")]
    elif paywalled_filter == "paywalled":
        data = [a for a in data if a.get("paywalled")]

    paywalled_count = sum(1 for a in data if a.get("paywalled"))

    return jsonify(
        {
            "articles":         data,
            "total":            len(data),
            "free_count":       len(data) - paywalled_count,
            "paywalled_count":  paywalled_count,
            "cache_age_minutes": cache_age,
            "week_offset":      week_offset,
            "week_start":       week_start.isoformat() if week_start else None,
            "week_end":         week_end.isoformat()   if week_end   else None,
        }
    )


@app.route("/api/weeks")
def weeks():
    return jsonify(get_available_weeks())


@app.route("/api/refresh", methods=["POST"])
def refresh():
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return jsonify({"status": "already_refreshing"})
        _refreshing = True

    def do_refresh():
        global _refreshing
        try:
            get_articles(force_refresh=True)
        finally:
            _refreshing = False

    threading.Thread(target=do_refresh, daemon=True).start()
    return jsonify({"status": "refreshing"})


@app.route("/api/status")
def status():
    return jsonify(
        {
            "refreshing": _refreshing,
            "cache_age_minutes": get_cache_age_minutes(),
        }
    )


@app.route("/api/digest")
def digest():
    """Return one-paragraph topic digests for the week."""
    all_articles = get_articles()
    result = {}
    for topic in TOPICS:
        topic_articles = [a for a in all_articles if a.get("topic") == topic["id"]][:5]
        result[topic["id"]] = {
            "name": topic["name"],
            "icon": topic["icon"],
            "color": topic["color"],
            "count": len([a for a in all_articles if a.get("topic") == topic["id"]]),
            "top": [
                {
                    "title": a["title"],
                    "url": a["url"],
                    "source": a.get("source", ""),
                    "published_rel": a.get("published_rel", ""),
                    "paywalled": a.get("paywalled", False),
                }
                for a in topic_articles
            ],
        }
    return jsonify(result)


if __name__ == "__main__":
    # Pre-warm cache on startup (background)
    threading.Thread(target=get_articles, daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000)
