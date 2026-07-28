"""User feedback/suggestions: a small public form, stored in its own
SQLite file (same pattern as analytics.py/auth.py), viewable only on the
admin dashboard. No email service, no third-party form provider -- just a
table and an admin-only list view.
"""
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                name TEXT,
                email TEXT,
                message TEXT NOT NULL,
                page TEXT,
                ip TEXT
            )
        """)


def submit_feedback(name, email, message, page, ip):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO feedback (ts, name, email, message, page, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), (name or "").strip()[:200],
             (email or "").strip()[:200], message.strip()[:4000], (page or "").strip()[:300], ip),
        )


def list_feedback(limit=200):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_feedback():
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"]
