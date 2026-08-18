"""Sports-prediction unlock gate: join the Telegram channel once, unlock
every sport's prediction pages for that visitor. Own SQLite file, same
pattern as feedback.py/analytics.py.

A visitor is identified by an opaque, signed cookie value (visitor_id) --
see app.py's _visitor_id() helper for how that cookie is minted/read. This
module only knows about that opaque string, never about cookies/Flask.
"""
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unlocks.db")

# A deep-link code is single-purpose and short-lived -- if the visitor never
# taps "Verify", it just expires and a fresh page load mints a new one.
CODE_TTL_SECONDS = 15 * 60


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unlocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                visitor_id TEXT UNIQUE NOT NULL,
                code TEXT,
                code_expires_at TEXT,
                telegram_user_id TEXT,
                telegram_username TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                unlocked_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unlocks_code ON unlocks(code)")


def new_visitor_id() -> str:
    return secrets.token_urlsafe(18)


def _row_for_visitor(conn, visitor_id: str):
    return conn.execute(
        "SELECT * FROM unlocks WHERE visitor_id=?", (visitor_id,),
    ).fetchone()


def is_unlocked(visitor_id: str) -> bool:
    if not visitor_id:
        return False
    with get_db() as conn:
        row = _row_for_visitor(conn, visitor_id)
    return bool(row and row["status"] == "unlocked")


def get_or_create_code(visitor_id: str) -> str:
    """One live code per visitor. Re-requesting before it expires returns
    the same code rather than orphaning the old one. Returns "" if the
    visitor is already unlocked (nothing to verify)."""
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        row = _row_for_visitor(conn, visitor_id)
        if row and row["status"] == "unlocked":
            return ""
        if row and row["code"] and row["code_expires_at"]:
            try:
                expires = datetime.fromisoformat(row["code_expires_at"])
            except ValueError:
                expires = now
            if expires > now:
                return row["code"]
        code = secrets.token_urlsafe(12)
        expires_at = (now + timedelta(seconds=CODE_TTL_SECONDS)).isoformat()
        if row:
            conn.execute(
                "UPDATE unlocks SET code=?, code_expires_at=? WHERE visitor_id=?",
                (code, expires_at, visitor_id),
            )
        else:
            conn.execute(
                "INSERT INTO unlocks (visitor_id, code, code_expires_at, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (visitor_id, code, expires_at, now.isoformat()),
            )
        return code


def resolve_code(code: str) -> dict | None:
    """Row dict for a live (unexpired, not-yet-consumed) code, or None."""
    if not code:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM unlocks WHERE code=? AND status='pending'", (code,),
        ).fetchone()
    if not row:
        return None
    try:
        expires = datetime.fromisoformat(row["code_expires_at"] or "")
    except ValueError:
        return None
    if expires <= datetime.now(timezone.utc):
        return None
    return dict(row)


def mark_unlocked(visitor_id: str, *, telegram_user_id: str, telegram_username: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE unlocks
            SET status='unlocked', unlocked_at=?, telegram_user_id=?,
                telegram_username=?, code=NULL, code_expires_at=NULL
            WHERE visitor_id=?
            """,
            (now, telegram_user_id, telegram_username, visitor_id),
        )


def count_unlocked() -> int:
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM unlocks WHERE status='unlocked'",
        ).fetchone()["n"]


def count_pending() -> int:
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM unlocks WHERE status='pending'",
        ).fetchone()["n"]
