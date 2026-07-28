"""Lightweight, dependency-free site-traffic logging for the admin
dashboard: one row per page request (timestamp, IP, path, method, user
agent, referrer), stored in its own SQLite file so it never touches
users.db. No third-party analytics service, no cookies, no client-side JS
-- purely server-side request logging, queried by app.py's /admin routes.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics.db")

# Requests to these path prefixes are noise for a traffic dashboard (static
# assets, the dashboard's own polling, health checks) -- excluded so the
# numbers reflect real visitor activity, not asset fetches.
EXCLUDED_PREFIXES = ("/static/", "/admin", "/favicon.ico")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                ip TEXT NOT NULL,
                path TEXT NOT NULL,
                method TEXT NOT NULL,
                status INTEGER,
                user_agent TEXT,
                referrer TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_ip ON visits(ip)")


def should_log(path):
    return not any(path.startswith(p) for p in EXCLUDED_PREFIXES)


def log_visit(ip, path, method, status, user_agent, referrer):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO visits (ts, ip, path, method, status, user_agent, referrer) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), ip, path, method, status,
             (user_agent or "")[:500], (referrer or "")[:500]),
        )


def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def summary(days=30):
    with get_db() as conn:
        since = _since(days)
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM visits WHERE ts >= ?", (since,),
        ).fetchone()["n"]
        unique_visitors = conn.execute(
            "SELECT COUNT(DISTINCT ip) AS n FROM visits WHERE ts >= ?", (since,),
        ).fetchone()["n"]
        today_since = _since(1)
        today_total = conn.execute(
            "SELECT COUNT(*) AS n FROM visits WHERE ts >= ?", (today_since,),
        ).fetchone()["n"]
        today_unique = conn.execute(
            "SELECT COUNT(DISTINCT ip) AS n FROM visits WHERE ts >= ?", (today_since,),
        ).fetchone()["n"]
    return {
        "total_visits": total, "unique_visitors": unique_visitors,
        "today_visits": today_total, "today_unique_visitors": today_unique,
        "window_days": days,
    }


def visits_per_day(days=14):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS visits, COUNT(DISTINCT ip) AS unique_visitors "
            "FROM visits WHERE ts >= ? GROUP BY day ORDER BY day",
            (_since(days),),
        ).fetchall()
    return [dict(r) for r in rows]


def top_pages(days=30, limit=15):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT path, COUNT(*) AS visits FROM visits WHERE ts >= ? "
            "GROUP BY path ORDER BY visits DESC LIMIT ?",
            (_since(days), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def top_visitors(days=30, limit=25):
    """Distinct IPs ranked by request volume, with their most recent visit
    and how many different pages they've hit -- the closest thing to a
    "who's actually browsing the site" view without cookies/accounts."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ip, COUNT(*) AS visits, COUNT(DISTINCT path) AS distinct_pages, "
            "MAX(ts) AS last_seen, MIN(ts) AS first_seen "
            "FROM visits WHERE ts >= ? GROUP BY ip ORDER BY visits DESC LIMIT ?",
            (_since(days), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_visits(limit=100):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ts, ip, path, method, status, user_agent, referrer "
            "FROM visits ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
