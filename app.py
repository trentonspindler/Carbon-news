"""
Carbon Markets Daily — Flask application
"""

import logging
import threading

from flask import Flask, jsonify, render_template, request

from news_fetcher import TOPICS, get_articles, get_cache_age_minutes

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = Flask(__name__)

_refresh_lock = threading.Lock()
_refreshing = False


@app.route("/")
def index():
    return render_template("index.html", topics=TOPICS)


@app.route("/api/articles")
def articles():
    topic = request.args.get("topic", "all").strip()
    search = request.args.get("search", "").lower().strip()
    paywalled_filter = request.args.get("paywalled", "all")  # all | free | paywalled

    data = get_articles()

    if topic != "all":
        data = [a for a in data if a.get("topic") == topic]

    if search:
        data = [
            a
            for a in data
            if search in (a.get("title") or "").lower()
            or search in (a.get("summary") or "").lower()
            or search in (a.get("source") or "").lower()
        ]

    if paywalled_filter == "free":
        data = [a for a in data if not a.get("paywalled")]
    elif paywalled_filter == "paywalled":
        data = [a for a in data if a.get("paywalled")]

    paywalled_count = sum(1 for a in data if a.get("paywalled"))

    return jsonify(
        {
            "articles": data,
            "total": len(data),
            "free_count": len(data) - paywalled_count,
            "paywalled_count": paywalled_count,
            "cache_age_minutes": get_cache_age_minutes(),
        }
    )


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
