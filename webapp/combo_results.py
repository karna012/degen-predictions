"""Persist and settle published Best Combos slips.

Snapshots the public combo slate once it is rebuilt (insert-if-missing by
signature), then grades legs against finished scores. The public track-record
page is admin-only; admin can still force-settle.

Channel posts (admin Send to Telegram + morning digest) are also recorded as
``source=degen_team`` with Telegram message ids so settled WON/LOST can be
replied under the original channel post. Those rows stay excluded from public
results, admin combo boards, and live model feedback.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from typing import Any

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combo_results.db")
logger = logging.getLogger(__name__)

# Slip / leg outcomes
PENDING = "pending"
WON = "won"
LOST = "lost"
PUSH = "push"  # void / push (e.g. DNB draw, O/U exact line)
VOID = "void"  # cannot settle (missing market data after final)

# A leg still unresolvable this many days after kickoff gets force-voided by
# settle_pending() rather than blocking its slip forever (see
# _leg_kickoff_age_days / _lookback_days_for).
STUCK_PENDING_VOID_DAYS = 5

# Provenance
SOURCE_BOARD = "board"
SOURCE_DEGEN_TEAM = "degen_team"
BUCKET_DEGEN_TEAM = "degen_team"
SETTLE_STATUSES = (WON, LOST, VOID, PUSH)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS combo_slips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slate_date TEXT NOT NULL,
                bucket TEXT NOT NULL,
                size INTEGER NOT NULL,
                signature TEXT NOT NULL,
                mix_kind TEXT,
                mix_label TEXT,
                joint_prob REAL,
                is_soft_fill INTEGER DEFAULT 0,
                is_fallback INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                snapped_at TEXT NOT NULL,
                settled_at TEXT,
                UNIQUE(slate_date, signature)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS combo_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slip_id INTEGER NOT NULL,
                leg_index INTEGER NOT NULL,
                sport_key TEXT,
                league_id TEXT,
                league_label TEXT,
                matchup TEXT,
                home TEXT,
                away TEXT,
                kickoff_iso TEXT,
                market TEXT,
                pick TEXT,
                confidence REAL,
                url TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                actual_note TEXT,
                settled_at TEXT,
                FOREIGN KEY(slip_id) REFERENCES combo_slips(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_combo_legs_slip ON combo_legs(slip_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_combo_slips_date ON combo_slips(slate_date)"
        )
        # Single-leg published picks (Today's Picks / combo pool), for feedback.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS published_picks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slate_date TEXT NOT NULL,
                signature TEXT NOT NULL,
                sport_key TEXT,
                league_id TEXT,
                league_label TEXT,
                matchup TEXT,
                home TEXT,
                away TEXT,
                kickoff_iso TEXT,
                market TEXT,
                pick TEXT,
                confidence REAL,
                url TEXT,
                model_version TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                actual_note TEXT,
                snapped_at TEXT NOT NULL,
                settled_at TEXT,
                UNIQUE(slate_date, signature)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_published_picks_date "
            "ON published_picks(slate_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_published_picks_status "
            "ON published_picks(status)"
        )
        _migrate_degen_team_columns(conn)
        _migrate_channel_serials(conn)


def _table_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn, table: str, column: str, typedef: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")


def _migrate_degen_team_columns(conn) -> None:
    """Add provenance columns for Degen Team Telegram slips (idempotent)."""
    for col, typedef in (
        ("source", "TEXT NOT NULL DEFAULT 'board'"),
        ("title", "TEXT"),
        ("telegram_message_ids", "TEXT"),
        ("telegram_result_posted_at", "TEXT"),
        ("twitter_result_posted_at", "TEXT"),
        ("uid", "TEXT"),
        ("result_blocked_at", "TEXT"),
    ):
        _ensure_column(conn, "combo_slips", col, typedef)
        _ensure_column(conn, "published_picks", col, typedef)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_combo_slips_source "
        "ON combo_slips(source)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_published_picks_source "
        "ON published_picks(source)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_combo_slips_uid "
        "ON combo_slips(uid) WHERE uid IS NOT NULL AND uid != ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_published_picks_uid "
        "ON published_picks(uid) WHERE uid IS NOT NULL AND uid != ''"
    )


def _migrate_channel_serials(conn) -> None:
    """Monotonic Telegram / RESULT serials for admin-sent channel slips."""
    for col in ("telegram_serial", "result_serial"):
        _ensure_column(conn, "combo_slips", col, "INTEGER")
        _ensure_column(conn, "published_picks", col, "INTEGER")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_serials (
            name TEXT PRIMARY KEY,
            next_value INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO channel_serials(name, next_value) "
        "VALUES ('telegram', 1), ('result', 1)"
    )
    _backfill_telegram_serials(conn)


def _backfill_telegram_serials(conn) -> None:
    """Give existing admin slips a Telegram serial, oldest first."""
    items = []
    for table in ("combo_slips", "published_picks"):
        rows = conn.execute(
            f"""
            SELECT id, snapped_at FROM {table}
            WHERE source=? AND IFNULL(telegram_serial, 0)=0
            """,
            (SOURCE_DEGEN_TEAM,),
        ).fetchall()
        for row in rows:
            items.append((row["snapped_at"] or "", table, int(row["id"])))
    if not items:
        return
    items.sort()
    row = conn.execute(
        "SELECT next_value FROM channel_serials WHERE name='telegram'"
    ).fetchone()
    n = int(row["next_value"]) if row else 1
    for _snapped, table, rid in items:
        conn.execute(
            f"UPDATE {table} SET telegram_serial=? WHERE id=?",
            (n, rid),
        )
        n += 1
    conn.execute(
        "UPDATE channel_serials SET next_value=? WHERE name='telegram'",
        (n,),
    )


def format_telegram_serial(n) -> str:
    try:
        value = int(n)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return f"#T-{value:05d}"


def format_result_serial(n) -> str:
    try:
        value = int(n)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return f"#R-{value:05d}"


def allocate_channel_serial(name: str) -> int:
    """Next Telegram or RESULT serial. Gaps are allowed if a send fails."""
    key = (name or "").strip().lower()
    if key not in ("telegram", "result"):
        raise ValueError(f"unknown serial name: {name}")
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO channel_serials(name, next_value) VALUES (?, 1)",
            (key,),
        )
        row = conn.execute(
            "SELECT next_value FROM channel_serials WHERE name=?",
            (key,),
        ).fetchone()
        n = int(row["next_value"]) if row else 1
        conn.execute(
            "UPDATE channel_serials SET next_value=? WHERE name=?",
            (n + 1, key),
        )
        return n


def ensure_result_serial(kind: str, row_id: int) -> int | None:
    """Assign a RESULT serial once per admin betslip; reuse on retries."""
    init_db()
    table = (
        "combo_slips"
        if (kind or "").strip().lower() == "combo"
        else "published_picks"
    )
    with get_db() as conn:
        row = conn.execute(
            f"SELECT result_serial FROM {table} WHERE id=?",
            (int(row_id),),
        ).fetchone()
        if not row:
            return None
        existing = row["result_serial"]
        if existing:
            try:
                return int(existing)
            except (TypeError, ValueError):
                pass
    n = allocate_channel_serial("result")
    with get_db() as conn:
        row = conn.execute(
            f"SELECT result_serial FROM {table} WHERE id=?",
            (int(row_id),),
        ).fetchone()
        if not row:
            return n
        existing = row["result_serial"]
        if existing:
            try:
                return int(existing)
            except (TypeError, ValueError):
                pass
        conn.execute(
            f"UPDATE {table} SET result_serial=? WHERE id=?",
            (n, int(row_id)),
        )
    return n


def stamp_result_serial(item: dict | None) -> dict:
    """Put a stable RESULT serial on a due item before rendering / sending."""
    item = dict(item or {})
    row_id = item.get("id")
    if not row_id:
        return item
    n = ensure_result_serial(item.get("kind") or "single", int(row_id))
    if n:
        item["result_serial"] = n
    return item


def slate_date_utc(now=None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.date().isoformat()


def _leg_signature_parts(leg: dict) -> tuple:
    return (
        (leg.get("sport_key") or "").strip().lower(),
        (leg.get("league_id") or "").strip().lower(),
        (leg.get("matchup") or "").strip().lower(),
        (leg.get("market") or "").strip().lower(),
        (leg.get("pick") or "").strip().lower(),
    )


def combo_signature(combo: dict) -> str:
    parts = sorted(_leg_signature_parts(leg) for leg in (combo.get("legs") or []))
    raw = json.dumps(parts, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _iter_published(combos: dict):
    """Yield (bucket, combo) for every published slip on the public board."""
    mapping = (
        ("best", 2),
        ("best", 3),
        ("mixed", "mixed_2"),
        ("mixed", "mixed_3"),
    )
    for bucket, key in mapping:
        for combo in combos.get(key) or []:
            yield bucket, combo


def snapshot_published_combos(combos: dict | None, *, slate_date: str | None = None) -> int:
    """Insert new published slips for ``slate_date`` (idempotent by signature).

    Returns the number of newly inserted slips.
    """
    init_db()
    if not combos:
        return 0
    day = slate_date or slate_date_utc()
    snapped_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    inserted = 0
    with get_db() as conn:
        for bucket, combo in _iter_published(combos):
            legs = combo.get("legs") or []
            if len(legs) < 2:
                continue
            sig = combo_signature(combo)
            existing = conn.execute(
                "SELECT id FROM combo_slips WHERE slate_date=? AND signature=?",
                (day, sig),
            ).fetchone()
            if existing:
                continue
            cur = conn.execute(
                """
                INSERT INTO combo_slips (
                    slate_date, bucket, size, signature, mix_kind, mix_label,
                    joint_prob, is_soft_fill, is_fallback, status, snapped_at,
                    source, title, uid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    bucket,
                    int(combo.get("size") or len(legs)),
                    sig,
                    combo.get("mix_kind") or "",
                    combo.get("mix_label") or "",
                    float(combo.get("joint_prob") or 0),
                    1 if combo.get("is_soft_fill") else 0,
                    1 if combo.get("is_fallback") else 0,
                    PENDING,
                    snapped_at,
                    SOURCE_BOARD,
                    "",
                    "",
                ),
            )
            slip_id = cur.lastrowid
            for idx, leg in enumerate(legs):
                conn.execute(
                    """
                    INSERT INTO combo_legs (
                        slip_id, leg_index, sport_key, league_id, league_label,
                        matchup, home, away, kickoff_iso, market, pick,
                        confidence, url, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slip_id,
                        idx,
                        leg.get("sport_key") or "",
                        leg.get("league_id") or "",
                        leg.get("league_label") or "",
                        leg.get("matchup") or "",
                        leg.get("home") or "",
                        leg.get("away") or "",
                        leg.get("kickoff_iso") or "",
                        leg.get("market") or "",
                        leg.get("pick") or "",
                        float(leg["confidence"]) if leg.get("confidence") is not None else None,
                        leg.get("url") or "",
                        PENDING,
                    ),
                )
            inserted += 1
    if inserted:
        logger.info("combo_results: snapped %s new slips for %s", inserted, day)
    return inserted


def pick_signature(leg: dict) -> str:
    raw = json.dumps(_leg_signature_parts(leg), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def snapshot_published_picks(
    legs: list | None,
    *,
    slate_date: str | None = None,
    model_version: str = "",
) -> int:
    """Insert new published single-leg picks (idempotent by signature)."""
    init_db()
    if not legs:
        return 0
    day = slate_date or slate_date_utc()
    snapped_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    inserted = 0
    with get_db() as conn:
        for leg in legs:
            if not leg:
                continue
            sig = pick_signature(leg)
            existing = conn.execute(
                "SELECT id FROM published_picks WHERE slate_date=? AND signature=?",
                (day, sig),
            ).fetchone()
            if existing:
                continue
            conf = leg.get("confidence")
            conn.execute(
                """
                INSERT INTO published_picks (
                    slate_date, signature, sport_key, league_id, league_label,
                    matchup, home, away, kickoff_iso, market, pick,
                    confidence, url, model_version, status, snapped_at,
                    source, title, uid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day,
                    sig,
                    leg.get("sport_key") or "",
                    leg.get("league_id") or "",
                    leg.get("league_label") or "",
                    leg.get("matchup") or "",
                    leg.get("home") or "",
                    leg.get("away") or "",
                    leg.get("kickoff_iso") or "",
                    leg.get("market") or "",
                    leg.get("pick") or "",
                    float(conf) if conf is not None else None,
                    leg.get("url") or "",
                    model_version or "",
                    PENDING,
                    snapped_at,
                    SOURCE_BOARD,
                    "",
                    "",
                ),
            )
            inserted += 1
    if inserted:
        logger.info("combo_results: snapped %s new picks for %s", inserted, day)
    return inserted


def _new_uid() -> str:
    return uuid.uuid4().hex


def _normalize_leg_for_store(leg: dict) -> dict:
    """Map public/admin leg dicts into combo_legs / published_picks fields."""
    if not isinstance(leg, dict):
        return {}
    conf = leg.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_f = None
    matchup = (leg.get("matchup") or "").strip()
    if not matchup:
        away = (leg.get("away") or "").strip()
        home = (leg.get("home") or "").strip()
        if home and away:
            matchup = f"{home} vs {away}"
        elif away and home:
            matchup = f"{away} vs {home}"
    return {
        "sport_key": (leg.get("sport_key") or "").strip(),
        "league_id": (leg.get("league_id") or "").strip(),
        "league_label": (leg.get("league_label") or leg.get("sport_label") or "").strip(),
        "matchup": matchup,
        "home": (leg.get("home") or "").strip(),
        "away": (leg.get("away") or "").strip(),
        "kickoff_iso": (leg.get("kickoff_iso") or "").strip(),
        "market": (leg.get("market") or "").strip(),
        "pick": (leg.get("pick") or "").strip(),
        "confidence": conf_f,
        "url": (leg.get("url") or "").strip(),
    }


def _tg_ids_json(ids) -> str:
    if not ids:
        return "[]"
    out = []
    for x in ids:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return json.dumps(out)


def record_degen_team_slip(
    legs: list[dict] | None,
    *,
    title: str = "",
    telegram_message_ids: list | None = None,
    slate_date: str | None = None,
    uid: str | None = None,
    telegram_serial: int | None = None,
) -> dict:
    """Persist a channel slip for settle + Telegram result replies -- retries
    on transient SQLite lock contention rather than losing an already-posted
    Telegram slip's ledger record over a single busy-database moment (seen
    in production: the Telegram post succeeds, this silently doesn't)."""
    import time

    last_exc = None
    for attempt in range(4):
        try:
            return _record_degen_team_slip_once(
                legs, title=title, telegram_message_ids=telegram_message_ids,
                slate_date=slate_date, uid=uid, telegram_serial=telegram_serial,
            )
        except sqlite3.OperationalError as exc:
            last_exc = exc
            logger.warning(
                "record_degen_team_slip: attempt %d hit %s, retrying",
                attempt + 1, exc,
            )
            time.sleep(0.3 * (attempt + 1))
    raise last_exc


def _record_degen_team_slip_once(
    legs: list[dict] | None,
    *,
    title: str = "",
    telegram_message_ids: list | None = None,
    slate_date: str | None = None,
    uid: str | None = None,
    telegram_serial: int | None = None,
) -> dict:
    init_db()
    cleaned = [_normalize_leg_for_store(leg) for leg in (legs or [])]
    cleaned = [leg for leg in cleaned if leg.get("pick") or leg.get("matchup")]
    if not cleaned:
        return {"ok": False, "error": "no_legs", "kind": None, "id": None, "uid": None}

    day = slate_date or slate_date_utc()
    snapped_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    uid = (uid or "").strip() or _new_uid()
    title = (title or "").strip()
    tg_json = _tg_ids_json(telegram_message_ids)
    try:
        serial = int(telegram_serial) if telegram_serial else 0
    except (TypeError, ValueError):
        serial = 0
    if serial <= 0:
        serial = allocate_channel_serial("telegram")
    n = len(cleaned)

    with get_db() as conn:
        if n == 1:
            leg = cleaned[0]
            base_sig = pick_signature(leg)
            sig = hashlib.sha1(f"{base_sig}:{uid}".encode("utf-8")).hexdigest()
            cur = conn.execute(
                """
                INSERT INTO published_picks (
                    slate_date, signature, sport_key, league_id, league_label,
                    matchup, home, away, kickoff_iso, market, pick,
                    confidence, url, model_version, status, snapped_at,
                    source, title, telegram_message_ids, uid, telegram_serial
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day, sig,
                    leg["sport_key"], leg["league_id"], leg["league_label"],
                    leg["matchup"], leg["home"], leg["away"], leg["kickoff_iso"],
                    leg["market"], leg["pick"], leg["confidence"], leg["url"],
                    "degen_team", PENDING, snapped_at,
                    SOURCE_DEGEN_TEAM, title or "Degen Team single",
                    tg_json, uid, serial,
                ),
            )
            logger.info(
                "degen_team: recorded single pick id=%s uid=%s date=%s",
                cur.lastrowid, uid, day,
            )
            return {
                "ok": True, "kind": "single", "id": cur.lastrowid,
                "uid": uid, "size": 1, "slate_date": day,
                "telegram_serial": serial,
            }

        combo = {"legs": cleaned, "size": n}
        base_sig = combo_signature(combo)
        sig = hashlib.sha1(f"{base_sig}:{uid}".encode("utf-8")).hexdigest()
        joint = 1.0
        has_joint = True
        for leg in cleaned:
            if leg.get("confidence") is None:
                has_joint = False
                break
            joint *= float(leg["confidence"])
        cur = conn.execute(
            """
                INSERT INTO combo_slips (
                    slate_date, bucket, size, signature, mix_kind, mix_label,
                    joint_prob, is_soft_fill, is_fallback, status, snapped_at,
                    source, title, telegram_message_ids, uid, telegram_serial
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day, BUCKET_DEGEN_TEAM, n, sig,
                    "degen_team", title or f"Degen Team {n}-leg",
                    float(joint) if has_joint else 0.0,
                    0, 0, PENDING, snapped_at,
                    SOURCE_DEGEN_TEAM, title or f"Degen Team {n}-leg",
                    tg_json, uid, serial,
                ),
        )
        slip_id = cur.lastrowid
        for idx, leg in enumerate(cleaned):
            conn.execute(
                """
                INSERT INTO combo_legs (
                    slip_id, leg_index, sport_key, league_id, league_label,
                    matchup, home, away, kickoff_iso, market, pick,
                    confidence, url, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slip_id, idx,
                    leg["sport_key"], leg["league_id"], leg["league_label"],
                    leg["matchup"], leg["home"], leg["away"], leg["kickoff_iso"],
                    leg["market"], leg["pick"], leg["confidence"], leg["url"],
                    PENDING,
                ),
            )
        logger.info(
            "degen_team: recorded combo slip id=%s uid=%s size=%s date=%s",
            slip_id, uid, n, day,
        )
        return {
            "ok": True, "kind": "combo", "id": slip_id,
            "uid": uid, "size": n, "slate_date": day,
            "telegram_serial": serial,
        }


def record_degen_team_from_telegram_slips(
    slips: list[dict] | None,
    *,
    slate_date: str | None = None,
) -> list[dict]:
    """Persist every successfully posted Telegram slip image."""
    out = []
    for slip in slips or []:
        if not isinstance(slip, dict):
            continue
        legs = slip.get("legs") or []
        if not legs:
            continue
        tg_ids = []
        mid = slip.get("telegram_message_id")
        if mid is not None:
            tg_ids.append(mid)
        for extra in slip.get("telegram_message_ids") or []:
            tg_ids.append(extra)
        # Results board is Telegram-sent only — skip slips with no channel message id.
        if not parse_telegram_message_ids(tg_ids):
            continue
        title = (
            (slip.get("title") or "").strip()
            or (slip.get("bucket") or "").strip()
            or (slip.get("caption") or "").strip()
        )
        # Strip day suffix from captions like "2-leg slip · 2026-08-11"
        if " · " in title:
            title = title.split(" · ", 1)[0].strip()
        title = re.sub(r"^#T-\d+\s+", "", title)
        title = re.sub(r"^#R-\d+\s+", "", title)
        rec = record_degen_team_slip(
            legs,
            title=title,
            telegram_message_ids=tg_ids,
            slate_date=slate_date,
            telegram_serial=slip.get("telegram_serial"),
        )
        out.append(rec)
    return out


def parse_telegram_message_ids(raw) -> list[int]:
    """Normalize stored JSON / list / single id into unique int message ids."""
    if raw is None or raw == "":
        return []
    values: list = []
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
        values = [int(raw)]
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                return [int(text)]
            except ValueError:
                return []
        if isinstance(parsed, list):
            values = parsed
        else:
            values = [parsed]
    out: list[int] = []
    seen: set[int] = set()
    for x in values:
        try:
            mid = int(x)
        except (TypeError, ValueError):
            continue
        if mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _has_unposted_telegram_ids(row: dict) -> bool:
    if (row.get("telegram_result_posted_at") or "").strip():
        return False
    return bool(parse_telegram_message_ids(row.get("telegram_message_ids")))


def _needs_channel_result(row: dict, channel: str) -> bool:
    if (row.get("result_blocked_at") or "").strip():
        return False
    if not parse_telegram_message_ids(row.get("telegram_message_ids")):
        return False
    if channel == "any":
        return (
            not (row.get("telegram_result_posted_at") or "").strip()
            or not (row.get("twitter_result_posted_at") or "").strip()
        )
    col = (
        "twitter_result_posted_at" if channel == "twitter"
        else "telegram_result_posted_at"
    )
    return not (row.get(col) or "").strip()


def _channel_result_posted_clause(channel: str) -> str:
    base = "AND IFNULL(result_blocked_at, '') = ''"
    if channel == "twitter":
        return f"{base} AND IFNULL(twitter_result_posted_at, '') = ''"
    if channel == "any":
        return (
            f"{base} AND (IFNULL(telegram_result_posted_at, '') = '' "
            "OR IFNULL(twitter_result_posted_at, '') = '')"
        )
    return f"{base} AND IFNULL(telegram_result_posted_at, '') = ''"


def list_telegram_results_due(*, limit: int = 40, channel: str = "telegram") -> list[dict]:
    """Settled channel slips/picks that still need a RESULT post.

    ``channel`` is ``telegram`` (default), ``twitter``, or ``any``.
    """
    init_db()
    limit = max(1, min(int(limit), 100))
    channel = (channel or "telegram").strip().lower()
    if channel not in ("telegram", "twitter", "any"):
        channel = "telegram"
    posted_clause = _channel_result_posted_clause(channel)
    decided = (WON, LOST, VOID, PUSH)
    out: list[dict] = []
    with get_db() as conn:
        slips = conn.execute(
            f"""
            SELECT * FROM combo_slips
            WHERE status IN ({",".join("?" * len(decided))})
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
              {posted_clause}
            ORDER BY COALESCE(settled_at, snapped_at) DESC, id DESC
            LIMIT ?
            """,
            (*decided, limit),
        ).fetchall()
        for slip in slips:
            item = dict(slip)
            if not _needs_channel_result(item, channel):
                continue
            item["kind"] = "combo"
            item["legs"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM combo_legs WHERE slip_id=? ORDER BY leg_index",
                    (item["id"],),
                ).fetchall()
            ]
            out.append(item)

        picks = conn.execute(
            f"""
            SELECT * FROM published_picks
            WHERE status IN ({",".join("?" * len(decided))})
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
              {posted_clause}
            ORDER BY COALESCE(settled_at, snapped_at) DESC, id DESC
            LIMIT ?
            """,
            (*decided, limit),
        ).fetchall()
        for pick in picks:
            item = dict(pick)
            if not _needs_channel_result(item, channel):
                continue
            item["kind"] = "single"
            item["legs"] = [dict(pick)]
            out.append(item)

    out.sort(
        key=lambda r: (r.get("settled_at") or r.get("snapped_at") or "", r.get("id") or 0),
        reverse=True,
    )
    return out[:limit]


def mark_telegram_result_posted(kind: str, row_id: int) -> None:
    """Mark a settled channel row as having had its Telegram result reply sent."""
    init_db()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    table = "combo_slips" if (kind or "").strip().lower() == "combo" else "published_picks"
    with get_db() as conn:
        conn.execute(
            f"UPDATE {table} SET telegram_result_posted_at=? WHERE id=?",
            (now, int(row_id)),
        )


def mark_twitter_result_posted(kind: str, row_id: int) -> None:
    """Mark a settled channel row as having had its X result post sent."""
    init_db()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    table = "combo_slips" if (kind or "").strip().lower() == "combo" else "published_picks"
    with get_db() as conn:
        conn.execute(
            f"UPDATE {table} SET twitter_result_posted_at=? WHERE id=?",
            (now, int(row_id)),
        )


def count_telegram_loss_results_posted(*, day: str | None = None) -> int:
    """How many LOST channel RESULT replies were already sent for ``day``."""
    return _count_channel_loss_results_posted("telegram_result_posted_at", day=day)


def count_twitter_loss_results_posted(*, day: str | None = None) -> int:
    """How many LOST channel RESULT posts were already sent to X for ``day``."""
    return _count_channel_loss_results_posted("twitter_result_posted_at", day=day)


def _count_channel_loss_results_posted(column: str, *, day: str | None = None) -> int:
    init_db()
    day = (day or slate_date_utc()).strip()
    if column not in ("telegram_result_posted_at", "twitter_result_posted_at"):
        return 0
    with get_db() as conn:
        n_combo = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM combo_slips
            WHERE slate_date=? AND status=?
              AND IFNULL({column}, '') != ''
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
            """,
            (day, LOST),
        ).fetchone()["n"]
        n_pick = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM published_picks
            WHERE slate_date=? AND status=?
              AND IFNULL({column}, '') != ''
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
            """,
            (day, LOST),
        ).fetchone()["n"]
    return int(n_combo or 0) + int(n_pick or 0)


def select_telegram_results_for_channel(
    items: list[dict] | None,
    *,
    max_losses: int = 2,
    losses_already_posted: int = 0,
) -> tuple[list[dict], list[dict]]:
    """Audience filter: send every WON, at most ``max_losses`` LOST.

    Returns ``(to_post, to_skip)``. VOID/PUSH are skipped (not wins, not the
    limited loss showcase). Skipped rows should still be marked posted so they
    do not clog the queue.
    """
    items = [i for i in (items or []) if isinstance(i, dict)]
    wins: list[dict] = []
    losses: list[dict] = []
    other: list[dict] = []
    for item in items:
        st = (item.get("status") or "").strip().lower()
        if st == WON:
            wins.append(item)
        elif st == LOST:
            losses.append(item)
        else:
            other.append(item)

    def _loss_rank(item: dict) -> tuple:
        title = (item.get("title") or "").strip().lower()
        # Prefer the public board card, then bigger slips, then newest.
        prefer = 0 if "today" in title and "pick" in title else 1
        size = len(item.get("legs") or [])
        return (prefer, -size, item.get("settled_at") or "", -(item.get("id") or 0))

    losses_sorted = sorted(losses, key=_loss_rank)
    room = max(0, int(max_losses) - max(0, int(losses_already_posted)))
    loss_post = losses_sorted[:room]
    loss_skip = losses_sorted[room:]

    # Wins first (newest first already from due list), then capped losses.
    to_post = list(wins) + list(loss_post)
    to_skip = list(loss_skip) + list(other)
    return to_post, to_skip


def format_telegram_result_text(item: dict) -> str:
    """Short channel reply: RESULT WON/LOST + legs (no model %).

    Fixture lines always use ``Home vs Away`` and ``Home score - Away score``.
    """
    status = (item.get("status") or PENDING).strip().lower()
    label = {
        WON: "WON",
        LOST: "LOST",
        VOID: "VOID",
        PUSH: "PUSH",
    }.get(status, status.upper() or "RESULT")
    mark = {
        WON: "✅",
        LOST: "❌",
        VOID: "➖",
        PUSH: "➖",
    }.get(status, "•")
    title = (item.get("title") or "").strip() or (
        "Single" if item.get("kind") == "single" else "Slip"
    )
    day = (item.get("slate_date") or "").strip()
    rs_tag = format_result_serial(item.get("result_serial"))
    tg_tag = format_telegram_serial(item.get("telegram_serial"))
    head = f"{mark} RESULT: {label}"
    if rs_tag:
        head = f"{rs_tag} {head}"
    title_line = title if not day else f"{title} · {day}"
    if tg_tag:
        title_line = f"{tg_tag} {title_line}"
    lines = [head, title_line]
    try:
        import daily_digest
        event_name = daily_digest._event_name
        score_line = daily_digest._score_line
    except Exception:
        event_name = lambda leg: (leg.get("matchup") or "Match")
        score_line = lambda leg: ""
    for leg in item.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        matchup = event_name(leg)
        score = score_line(leg)
        pick = (leg.get("pick") or "").strip()
        market = (leg.get("market") or "").strip()
        leg_status = (leg.get("status") or status).strip().lower()
        leg_label = {
            WON: "WON", LOST: "LOST", VOID: "VOID", PUSH: "PUSH", PENDING: "PENDING",
        }.get(leg_status, leg_status.upper())
        bit = f"• {matchup}"
        if score:
            bit += f"\n  {score}"
        if pick:
            bit += f"\n  {pick}"
            if market:
                bit += f" · {market}"
        bit += f" → {leg_label}"
        lines.append(bit)
    try:
        import daily_digest
        url = daily_digest.site_utm("/todays-picks/combos", campaign="tg_result")
        if url:
            lines.extend(["", url])
    except Exception:
        pass
    text = "\n".join(lines).strip()
    return text[:4096]


def _normalize_status(status: str) -> str | None:
    s = (status or "").strip().lower()
    if s in (WON, LOST, VOID, PUSH, PENDING):
        return s
    aliases = {
        "hit": WON, "win": WON, "won": WON,
        "miss": LOST, "lose": LOST, "lost": LOST,
        "push": PUSH, "void": VOID, "cancel": VOID, "cancelled": VOID,
    }
    return aliases.get(s)


def manual_settle_combo_slip(
    slip_id: int,
    status: str,
    *,
    note: str = "",
    leg_statuses: list[str] | None = None,
) -> dict:
    """Admin grades a Degen Team (or any) combo slip Won/Lost/Void.

    If ``leg_statuses`` is provided (same length as legs), each leg is set
    individually and the slip status is rolled up; otherwise every pending
    leg inherits ``status``.
    """
    init_db()
    want = _normalize_status(status)
    if want not in SETTLE_STATUSES:
        return {"ok": False, "error": "bad_status"}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    note = (note or "").strip()
    with get_db() as conn:
        slip = conn.execute(
            "SELECT * FROM combo_slips WHERE id=?", (int(slip_id),)
        ).fetchone()
        if not slip:
            return {"ok": False, "error": "not_found"}
        legs = conn.execute(
            "SELECT * FROM combo_legs WHERE slip_id=? ORDER BY leg_index",
            (int(slip_id),),
        ).fetchall()
        if not legs:
            return {"ok": False, "error": "no_legs"}
        statuses: list[str] = []
        for i, leg in enumerate(legs):
            if leg_statuses is not None and i < len(leg_statuses):
                ls = _normalize_status(leg_statuses[i]) or want
            else:
                ls = want
            if ls not in SETTLE_STATUSES:
                ls = want
            statuses.append(ls)
            conn.execute(
                """
                UPDATE combo_legs
                SET status=?, actual_note=?, settled_at=?
                WHERE id=?
                """,
                (ls, note or f"admin:{ls}", now, leg["id"]),
            )
        slip_status = _roll_up_slip_status(statuses)
        if slip_status == PENDING:
            slip_status = want if want != PENDING else VOID
        conn.execute(
            "UPDATE combo_slips SET status=?, settled_at=? WHERE id=?",
            (slip_status, now, int(slip_id)),
        )
    return {"ok": True, "kind": "combo", "id": int(slip_id), "status": slip_status}


def manual_settle_pick(pick_id: int, status: str, *, note: str = "") -> dict:
    """Admin grades a published single pick (incl. Degen Team singles)."""
    init_db()
    want = _normalize_status(status)
    if want not in SETTLE_STATUSES:
        return {"ok": False, "error": "bad_status"}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    note = (note or "").strip() or f"admin:{want}"
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM published_picks WHERE id=?", (int(pick_id),)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}
        conn.execute(
            """
            UPDATE published_picks
            SET status=?, actual_note=?, settled_at=?
            WHERE id=?
            """,
            (want, note, now, int(pick_id)),
        )
    return {"ok": True, "kind": "single", "id": int(pick_id), "status": want}


def list_degen_team_items(
    *,
    days: int = 60,
    status: str | None = None,
    limit: int = 100,
) -> dict:
    """Pending/settled channel slips that were actually sent to Telegram.

    Rows without ``telegram_message_ids`` are hidden — those are X-only,
    failed sends, or legacy records that never hit the channel.
    """
    init_db()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).date().isoformat()
    combos_raw = list_slips(
        days=days, status=status, limit=max(limit * 3, limit),
        source=SOURCE_DEGEN_TEAM,
    )
    combos = [
        s for s in combos_raw
        if parse_telegram_message_ids(s.get("telegram_message_ids"))
    ][:limit]
    with get_db() as conn:
        params: list[Any] = [cutoff, SOURCE_DEGEN_TEAM]
        q = """
            SELECT * FROM published_picks
            WHERE slate_date>=? AND source=?
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
        """
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY slate_date DESC, id DESC LIMIT ?"
        params.append(int(limit))
        singles = [dict(r) for r in conn.execute(q, params).fetchall()]
        # Belt-and-suspenders: drop malformed JSON id blobs.
        singles = [
            s for s in singles
            if parse_telegram_message_ids(s.get("telegram_message_ids"))
        ]
    return {"combos": combos, "singles": singles}


def degen_team_summary(days: int = 30) -> dict:
    """Hit rate for Telegram-sent channel slips (combos + singles)."""
    init_db()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).date().isoformat()
    with get_db() as conn:
        combo_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM combo_slips
            WHERE slate_date>=? AND source=?
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
            GROUP BY status
            """,
            (cutoff, SOURCE_DEGEN_TEAM),
        ).fetchall()
        pick_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM published_picks
            WHERE slate_date>=? AND source=?
              AND IFNULL(telegram_message_ids, '') NOT IN ('', '[]')
            GROUP BY status
            """,
            (cutoff, SOURCE_DEGEN_TEAM),
        ).fetchall()
    combo_counts = {r["status"]: r["n"] for r in combo_rows}
    cw = combo_counts.get(WON, 0)
    cl = combo_counts.get(LOST, 0)
    cp = combo_counts.get(PENDING, 0)
    cv = combo_counts.get(VOID, 0) + combo_counts.get(PUSH, 0)
    decided_c = cw + cl
    combo = {
        "days": days, "source": SOURCE_DEGEN_TEAM,
        "won": cw, "lost": cl, "pending": cp, "void": cv,
        "decided": decided_c,
        "hit_rate": (cw / decided_c) if decided_c else None,
        "total": cw + cl + cp + cv,
    }
    counts = {r["status"]: r["n"] for r in pick_rows}
    sw = counts.get(WON, 0)
    sl = counts.get(LOST, 0)
    sp = counts.get(PENDING, 0)
    sv = counts.get(VOID, 0) + counts.get(PUSH, 0)
    decided_s = sw + sl
    singles = {
        "won": sw, "lost": sl, "pending": sp, "void": sv,
        "decided": decided_s,
        "hit_rate": (sw / decided_s) if decided_s else None,
        "total": sw + sl + sp + sv,
    }
    tw = cw + sw
    tl = cl + sl
    tp = cp + sp
    tv = cv + sv
    decided = tw + tl
    return {
        "days": days,
        "combos": combo,
        "singles": singles,
        "won": tw,
        "lost": tl,
        "pending": tp,
        "void": tv,
        "decided": decided,
        "hit_rate": (tw / decided) if decided else None,
        "total": tw + tl + tp + tv,
    }


# ---------------------------------------------------------------------------
# Pure settlement helpers (unit-testable)
# ---------------------------------------------------------------------------

def _parse_ou(pick: str):
    """Return (side, line) from 'Over 2.5' / 'Under 8' style picks, or None."""
    m = re.search(r"(?i)\b(over|under)\s+([0-9]+(?:\.[0-9]+)?)\b", pick or "")
    if not m:
        return None
    return m.group(1).lower(), float(m.group(2))


def _settle_ou(side: str, line: float, actual: float) -> str:
    if abs(actual - line) < 1e-9:
        return PUSH
    hit = (actual > line) if side == "over" else (actual < line)
    return WON if hit else LOST


def _result_code(home_score: float, away_score: float) -> str:
    if home_score > away_score:
        return "H"
    if away_score > home_score:
        return "A"
    return "D"


def _score_note(home_score, away_score, *, extra: str = "") -> str:
    """Canonical settle note: ``Home score - Away score`` (+ optional suffix)."""
    def _fmt(v) -> str:
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)
        except (TypeError, ValueError):
            return str(v).strip()

    base = f"{_fmt(home_score)} - {_fmt(away_score)}"
    extra = (extra or "").strip()
    return f"{base} {extra}".strip() if extra else base


def _fold_name(text: str) -> str:
    import unicodedata
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", raw).strip().lower()


def _names_match(a: str, b: str) -> bool:
    aa = _fold_name(a)
    bb = _fold_name(b)
    if not aa or not bb:
        return False
    if aa == bb or aa in bb or bb in aa:
        return True
    # Last-token overlap: "Seibu Lions" vs "Saitama Seibu Lions"
    ta, tb = aa.split()[-1], bb.split()[-1]
    if len(ta) >= 4 and ta == tb:
        return True
    return False


# Display name <-> historical/Elo identity for cricket settle matching.
_CRICKET_NAME_ALIASES = {
    "jamaica kingsmen": "jamaica tallawahs",
    "barbados tridents": "barbados royals",
    "manchester super giants": "manchester originals",
    "sunrisers leeds": "northern superchargers",
    "mi london": "oval invincibles",
    "antigua & barbuda falcons": "antigua and barbuda falcons",
    "st kitts & nevis patriots": "st kitts and nevis patriots",
    "st lucia kings": "saint lucia kings",
}
for _src, _dst in list(_CRICKET_NAME_ALIASES.items()):
    _CRICKET_NAME_ALIASES.setdefault(_dst, _src)


def _cricket_names_match(a: str, b: str) -> bool:
    if _names_match(a, b):
        return True
    aa, bb = _fold_name(a), _fold_name(b)
    aa2 = _CRICKET_NAME_ALIASES.get(aa, aa)
    bb2 = _CRICKET_NAME_ALIASES.get(bb, bb)
    return _names_match(aa2, bb) or _names_match(aa, bb2) or _names_match(aa2, bb2)


def settle_leg_against_score(market: str, pick: str, score: dict) -> tuple[str, str]:
    """Grade one leg.

    ``score`` keys: home, away, home_score, away_score, completed,
    optional extras (home_corners, away_corners, winner_name, …).

    Returns (status, actual_note).
    """
    if not score or not score.get("completed"):
        return PENDING, ""

    market_l0 = (market or "").strip().lower()
    pick_s0 = (pick or "").strip()
    winner0 = score.get("winner_name")
    # Tennis (and some cricket) settle from an explicit winner when box scores
    # are non-numeric or only one side is filled.
    if winner0 and market_l0 in ("match winner", "moneyline", "upset lean"):
        hs0 = score.get("home_score")
        aws0 = score.get("away_score")
        if hs0 is not None and aws0 is not None:
            note0 = _score_note(hs0, aws0)
        elif hs0 is not None:
            note0 = str(hs0)
        else:
            note0 = f"W: {winner0}"
        return (WON if _cricket_names_match(pick_s0, winner0) else LOST), note0

    try:
        hs = float(score["home_score"])
        aws = float(score["away_score"])
    except (TypeError, ValueError, KeyError):
        # Cricket scores sometimes arrive as strings like "185/6".
        hs = score.get("home_score")
        aws = score.get("away_score")
        if hs is None or aws is None:
            return PENDING, "Waiting for numeric score"
        # Match-winner only path when totals cannot be parsed.
        hs_num = aws_num = None
        try:
            hs_num = float(str(hs).split("/")[0])
            aws_num = float(str(aws).split("/")[0])
        except (TypeError, ValueError):
            pass
        note = _score_note(hs, aws)
        market_l = (market or "").strip().lower()
        pick_s = (pick or "").strip()
        if market_l in ("match winner", "moneyline", "upset lean"):
            winner = score.get("winner_name")
            if winner:
                return (WON if _names_match(pick_s, winner) else LOST), note
            if hs_num is None or aws_num is None:
                return PENDING, note
            actual_winner = score.get("home") if hs_num > aws_num else score.get("away")
            if hs_num == aws_num:
                return LOST, note + " (tie)"
            return (WON if _names_match(pick_s, actual_winner or "") else LOST), note
        if hs_num is None or aws_num is None:
            return VOID, note + " (no settleable score)"
        hs, aws = hs_num, aws_num

    note = _score_note(hs, aws)
    market_l = (market or "").strip().lower()
    pick_s = (pick or "").strip()
    home = score.get("home") or ""
    away = score.get("away") or ""
    code = _result_code(hs, aws)
    total = hs + aws

    if market_l in ("moneyline", "match winner", "upset lean"):
        if code == "D":
            return LOST, note + " (draw)"
        winner = home if code == "H" else away
        return (WON if _names_match(pick_s, winner) else LOST), note

    if market_l == "safer result":
        # Exact: "Arsenal win" / DC: "Arsenal or Draw (1X)" / "Chelsea or Draw (X2)"
        dc = re.search(r"(?i)\((1x|x2)\)", pick_s)
        if dc or re.search(r"(?i)\bor draw\b", pick_s):
            code_m = (dc.group(1).upper() if dc else "")
            if code_m == "X2":
                hit = code in ("D", "A")
            elif code_m == "1X":
                hit = code in ("H", "D")
            else:
                fav = pick_s.split(" or ")[0].strip()
                if _names_match(fav, home):
                    hit = code in ("H", "D")
                elif _names_match(fav, away):
                    hit = code in ("D", "A")
                else:
                    return VOID, note
            return (WON if hit else LOST), note
        team = re.sub(r"(?i)\s+win\s*$", "", pick_s).strip()
        if code == "D":
            return LOST, note
        winner = home if code == "H" else away
        return (WON if _names_match(team, winner) else LOST), note

    if market_l == "draw no bet":
        team = re.sub(r"(?i)\s*\(dnb\)\s*$", "", pick_s).strip()
        if code == "D":
            return PUSH, note + " (void)"
        winner = home if code == "H" else away
        return (WON if _names_match(team, winner) else LOST), note

    if market_l == "both teams to score":
        btts = hs > 0 and aws > 0
        want_yes = pick_s.lower().startswith("yes")
        hit = btts if want_yes else (not btts)
        return (WON if hit else LOST), note + (" BTTS" if btts else " no BTTS")

    if market_l in ("total goals", "total runs", "game total", "total hits", "total strikeouts",
                    "total sets", "total games", "total points"):
        parsed = _parse_ou(pick_s)
        if not parsed:
            return VOID, note
        side, line = parsed
        if market_l == "total sets":
            actual = score.get("total_sets")
            if actual is None:
                return VOID, note + " (sets unavailable)"
            return _settle_ou(side, line, float(actual)), note + f" sets {actual}"
        if market_l == "total games":
            actual = score.get("total_games")
            if actual is None:
                return VOID, note + " (games unavailable)"
            return _settle_ou(side, line, float(actual)), note + f" games {actual}"
        if market_l in ("total hits", "total strikeouts"):
            extra = score.get("total_hits") if "hits" in market_l else score.get("total_strikeouts")
            if extra is None:
                return VOID, note + " (stat unavailable)"
            return _settle_ou(side, line, float(extra)), note + f" actual {extra}"
        return _settle_ou(side, line, float(total)), note + f" total {total:g}"

    if market_l == "player to win a set":
        home_sets = score.get("home_sets")
        away_sets = score.get("away_sets")
        if home_sets is None or away_sets is None:
            return VOID, note + " (sets unavailable)"
        if _names_match(pick_s, home):
            hit = int(home_sets) >= 1
        elif _names_match(pick_s, away):
            hit = int(away_sets) >= 1
        else:
            return VOID, note
        return (WON if hit else LOST), note + f" sets {home_sets}-{away_sets}"

    if market_l == "team to score":
        # "Arsenal to score"
        team = re.sub(r"(?i)\s+to score\s*$", "", pick_s).strip()
        if _names_match(team, home):
            hit = hs > 0
        elif _names_match(team, away):
            hit = aws > 0
        else:
            return VOID, note
        return (WON if hit else LOST), note

    if market_l in ("team runs", "team points"):
        # "Yankees Over 4.5"
        parsed = _parse_ou(pick_s)
        if not parsed:
            return VOID, note
        side, line = parsed
        if _names_match(home, pick_s):
            actual = hs
        elif _names_match(away, pick_s):
            actual = aws
        else:
            # Team name is the leading tokens before Over/Under
            m = re.match(r"^(.*?)\s+(?i:over|under)\b", pick_s)
            team = (m.group(1) if m else "").strip()
            if _names_match(team, home):
                actual = hs
            elif _names_match(team, away):
                actual = aws
            else:
                return VOID, note
        return _settle_ou(side, line, float(actual)), note

    if market_l in ("handicap", "spread"):
        # Soccer/baseball dog +0.5/+1.5; tennis games handicap +/−3.5 etc.
        m = re.search(r"([+-]\d+(?:\.\d+)?)\s*$", pick_s)
        if not m:
            return VOID, note
        signed = float(m.group(1))
        team = pick_s[: m.start()].strip()
        if _names_match(team, home):
            margin = (hs + signed) - aws
        elif _names_match(team, away):
            margin = (aws + signed) - hs
        else:
            return VOID, note
        if abs(margin) < 1e-9:
            return PUSH, note
        return (WON if margin > 0 else LOST), note

    if market_l == "corners":
        # "Arsenal (more corners)"
        hc = score.get("home_corners")
        ac = score.get("away_corners")
        if hc is None or ac is None:
            return VOID, note + " (corners unavailable)"
        note2 = f"{note}; corners {hc}-{ac}"
        if hc == ac:
            return LOST, note2 + " (tie)"
        winner = home if hc > ac else away
        team = re.sub(r"(?i)\s*\(more corners\)\s*$", "", pick_s).strip()
        return (WON if _names_match(team, winner) else LOST), note2

    if market_l == "total corners":
        parsed = _parse_ou(pick_s)
        hc = score.get("home_corners")
        ac = score.get("away_corners")
        if not parsed or hc is None or ac is None:
            return VOID, note + " (corners unavailable)"
        side, line = parsed
        actual = float(hc) + float(ac)
        return _settle_ou(side, line, actual), f"corners {actual:g}"

    return VOID, note + f" (unsupported: {market})"


def _roll_up_slip_status(leg_statuses: list[str]) -> str:
    if any(s == PENDING for s in leg_statuses):
        return PENDING
    # Void/push legs are ignored for the slip (treated as non-losing).
    active = [s for s in leg_statuses if s not in (VOID, PUSH)]
    if not active:
        return VOID
    if any(s == LOST for s in active):
        return LOST
    if all(s == WON for s in active):
        return WON
    return PENDING


# ---------------------------------------------------------------------------
# Score lookups
# ---------------------------------------------------------------------------

def _date_only(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return text[:10]


def _dates_within(a: str, b: str, days: int = 1) -> bool:
    da, db = _date_only(a), _date_only(b)
    if not da or not db:
        return False
    try:
        aa = datetime.date.fromisoformat(da)
        bb = datetime.date.fromisoformat(db)
    except ValueError:
        return da == db
    return abs((aa - bb).days) <= days


def _kickoff_date(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        raw = str(iso_str).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).date().isoformat()
    except (ValueError, TypeError):
        return str(iso_str)[:10]


def _leg_kickoff_age_days(kickoff_iso: str) -> int | None:
    """Days between a leg's kickoff and now, or None if unparseable."""
    day = _kickoff_date(kickoff_iso)
    if not day:
        return None
    try:
        kickoff = datetime.date.fromisoformat(day[:10])
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.timezone.utc).date() - kickoff).days


def _lookback_days_for(kickoff_iso: str, *, floor: int, cap: int = 45) -> int:
    """Widen a fixture-fetch lookback window to cover an old pending leg's
    own kickoff date, clamped to [floor, cap].

    The per-sport fixture fetchers (mlb_fixtures/nfl_fixtures/npb_fixtures/
    tennis_fixtures) default to a small trailing window (2-7 days) sized for
    "what's live/upcoming right now". Settlement lookups run on legs that
    can be arbitrarily older than that if a provider hiccup or name mismatch
    stalls one game -- without this, such a leg's fixture falls out of the
    window forever and the leg (and its whole slip) never settles.
    """
    age = _leg_kickoff_age_days(kickoff_iso)
    if age is None:
        return floor
    return max(floor, min(age + 1, cap))


def _parse_iso_utc(raw) -> datetime.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _pick_fixture_near_kickoff(hits: list, kickoff_iso: str):
    """Pick this kickoff's game, not an earlier final in the same series.

    ESPN MLB dates are UTC. A West Coast night game and the next afternoon
    game can share a UTC calendar date, so ``date[:10] == kickoff_date``
    plus "first completed" grades Tuesday's 4-1 onto Wednesday's pick.
    Closest timestamp wins. If kickoff is known we do not fall back to the
    first completed series game.
    """
    if not hits:
        return None
    kick = _parse_iso_utc(kickoff_iso)
    if kick is None:
        day = _kickoff_date(kickoff_iso) or _date_only(kickoff_iso)
        if day:
            exact = [m for m in hits if _date_only(m.get("date")) == day]
            near = [m for m in hits if _dates_within(m.get("date") or "", day, 1)]
            pool = exact or near or hits
        else:
            pool = hits
        done = next((m for m in pool if m.get("completed")), None)
        return done or pool[0]
    return min(
        hits,
        key=lambda m: abs(_parse_iso_utc(m.get("date")) - kick)
        if _parse_iso_utc(m.get("date")) is not None
        else datetime.timedelta(days=9999),
    )


def _soccer_team_match(a: str, b: str) -> bool:
    """Stricter than ``_names_match`` — avoid United/City/FC last-token collisions."""
    aa, bb = _fold_name(a), _fold_name(b)
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    # Strip generic club tokens then compare cores ("FC Dallas" ↔ "Dallas").
    generic = frozenset({
        "fc", "sc", "cf", "afc", "the", "club", "united", "city", "town",
        "hotspur", "athletic", "atletico", "real", "sporting",
    })
    def _core(text: str) -> str:
        parts = [p for p in text.split() if p not in generic]
        return " ".join(parts) if parts else text

    ca, cb = _core(aa), _core(bb)
    if ca and cb and ca == cb:
        return True
    short, long_ = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
    # Reject bare generics and very short stems ("diego" ⊂ "san diego fc").
    if len(short) < 6 or short in generic:
        return False
    if short in long_:
        return True
    # Core containment: "dallas" ⊂ "fc dallas" already handled via equality of cores.
    c_short, c_long = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    return bool(c_short) and len(c_short) >= 6 and c_short in c_long


def _soccer_pair_match(fx_home: str, fx_away: str, home: str, away: str) -> str | None:
    """Return 'same', 'swap', or None for orientation."""
    if _soccer_team_match(fx_home, home) and _soccer_team_match(fx_away, away):
        return "same"
    if _soccer_team_match(fx_home, away) and _soccer_team_match(fx_away, home):
        return "swap"
    return None


def _history_row_near_kickoff(row_date, kick: datetime.datetime | None) -> bool:
    """True when a football-data history Date is within ~1.5 days of kickoff."""
    if kick is None:
        return False
    text = str(row_date or "").strip()
    if not text:
        return False
    parsed = None
    for dayfirst in (False, True):
        try:
            import pandas as pd
            ts = pd.to_datetime(text, dayfirst=dayfirst, errors="coerce")
            if ts is not None and ts == ts:
                parsed = ts.to_pydatetime()
                break
        except Exception:
            continue
    if parsed is None:
        day = _date_only(text)
        if not day:
            return False
        try:
            parsed = datetime.datetime.fromisoformat(day)
        except ValueError:
            return False
    if parsed.tzinfo is None:
        # History dates are calendar days — compare on UTC/IST calendar windows.
        kick_dates = {
            kick.astimezone(datetime.timezone.utc).date(),
            kick.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date(),
        }
        return parsed.date() in kick_dates or any(
            abs((parsed.date() - d).days) <= 1 for d in kick_dates
        )
    parsed = parsed.astimezone(datetime.timezone.utc)
    return abs(parsed - kick) <= datetime.timedelta(hours=36)


def _lookup_soccer(leg: dict) -> dict | None:
    """Resolve FT score for one soccer leg — same fixture, not an older H2H."""
    league_id = leg.get("league_id") or ""
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    kickoff = leg.get("kickoff_iso") or ""
    kick = _parse_iso_utc(kickoff)

    hits = []
    try:
        import soccer_fixtures
        for fx in soccer_fixtures.get_feed(league_id) or []:
            orient = _soccer_pair_match(
                fx.get("home") or "", fx.get("away") or "", home, away,
            )
            if not orient:
                continue
            row = dict(fx)
            row["_orient"] = orient
            hits.append(row)
    except Exception:
        hits = []

    def _near(fx) -> bool:
        if kick is None:
            return True
        fx_dt = _parse_iso_utc(
            fx.get("date") or fx.get("DateUtc") or fx.get("kickoff_iso")
        )
        if fx_dt is None:
            return False
        return abs(fx_dt - kick) <= datetime.timedelta(hours=36)

    # Live fixture for this kickoff that is not final → never use history.
    unfinished_near = [
        h for h in hits
        if _near(h) and not (
            h.get("completed")
            and h.get("home_score") is not None
            and h.get("away_score") is not None
        )
    ]
    if unfinished_near:
        return None

    chosen = None
    if hits:
        if kick is None:
            done = [
                h for h in hits
                if h.get("completed")
                and h.get("home_score") is not None
                and h.get("away_score") is not None
            ]
            # Without a kickoff, only settle if exactly one completed H2H exists.
            chosen = done[0] if len(done) == 1 else None
        else:
            near_hits = [h for h in hits if _near(h)]
            chosen = _pick_fixture_near_kickoff(near_hits or hits, kickoff)
            if chosen is not None and not _near(chosen):
                chosen = None
    if chosen is not None:
        if not (chosen.get("completed") and chosen.get("home_score") is not None
                and chosen.get("away_score") is not None):
            return None
        # Keep feed orientation; settle matches picks by team name.
        return {
            "home": chosen.get("home"),
            "away": chosen.get("away"),
            "home_score": float(chosen["home_score"]),
            "away_score": float(chosen["away_score"]),
            "completed": True,
        }

    # History fallback only when kickoff is known and the row is near that day.
    # Never return the latest H2H from another week/season.
    if kick is None:
        return None
    try:
        import match_context
        from leagues import LEAGUES
    except Exception:
        return None
    if league_id not in LEAGUES:
        return None
    try:
        history = match_context._load_league_history(league_id)
    except Exception:
        return None
    if history is None or getattr(history, "empty", True):
        return None

    near_rows = []
    for _, row in history.iterrows():
        orient = _soccer_pair_match(
            str(row.get("HomeTeam") or ""),
            str(row.get("AwayTeam") or ""),
            home,
            away,
        )
        if not orient:
            continue
        if not _history_row_near_kickoff(row.get("Date"), kick):
            continue
        near_rows.append(row)
    if not near_rows:
        return None
    row = near_rows[-1]
    out = {
        "home": str(row["HomeTeam"]),
        "away": str(row["AwayTeam"]),
        "home_score": float(row["FTHG"]),
        "away_score": float(row["FTAG"]),
        "completed": True,
    }
    for col, key in (("HC", "home_corners"), ("AC", "away_corners")):
        if col in row.index and row[col] == row[col]:  # not NaN
            try:
                out[key] = float(row[col])
            except (TypeError, ValueError):
                pass
    return out


def _lookup_mlb(leg: dict) -> dict | None:
    try:
        from mlb_fixtures import get_fixtures
        days_back = _lookback_days_for(leg.get("kickoff_iso") or "", floor=2)
        fixtures = get_fixtures(days_back=days_back)
    except Exception:
        return None
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    hits = []
    for m in fixtures or []:
        if not (
            _names_match(m.get("home_team") or "", home)
            and _names_match(m.get("away_team") or "", away)
        ):
            continue
        hits.append(m)
    m = _pick_fixture_near_kickoff(hits, leg.get("kickoff_iso") or "")
    if not m:
        return None
    return {
        "home": m.get("home_team"),
        "away": m.get("away_team"),
        "home_score": m.get("home_score"),
        "away_score": m.get("away_score"),
        "completed": bool(m.get("completed")),
    }


def _lookup_nfl(leg: dict) -> dict | None:
    try:
        from nfl_fixtures import get_fixtures
        days_back = _lookback_days_for(leg.get("kickoff_iso") or "", floor=5)
        fixtures = get_fixtures(days_back=days_back)
    except Exception:
        return None
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    hits = []
    for m in fixtures or []:
        if not (
            _names_match(m.get("home_team") or "", home)
            and _names_match(m.get("away_team") or "", away)
        ):
            if not (
                _names_match(m.get("home_abbr") or "", home)
                and _names_match(m.get("away_abbr") or "", away)
            ):
                continue
        hits.append(m)
    m = _pick_fixture_near_kickoff(hits, leg.get("kickoff_iso") or "")
    if not m:
        return None
    return {
        "home": m.get("home_team"),
        "away": m.get("away_team"),
        "home_score": m.get("home_score"),
        "away_score": m.get("away_score"),
        "completed": bool(m.get("completed")),
    }


def _lookup_npb(leg: dict) -> dict | None:
    try:
        from npb_fixtures import get_fixtures
        days_back = _lookback_days_for(leg.get("kickoff_iso") or "", floor=7)
        fixtures = get_fixtures(days_back=days_back, resolve_pitchers=False)
    except Exception:
        return None
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    hits = []
    for m in fixtures or []:
        if not (
            _names_match(m.get("home_team") or "", home)
            and _names_match(m.get("away_team") or "", away)
        ):
            continue
        if m.get("postponed"):
            continue
        hits.append(m)
    m = _pick_fixture_near_kickoff(hits, leg.get("kickoff_iso") or "")
    if not m:
        return None
    return {
        "home": m.get("home_team"),
        "away": m.get("away_team"),
        "home_score": m.get("home_score"),
        "away_score": m.get("away_score"),
        "completed": bool(m.get("completed")),
    }


def _parse_cricket_runs(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    m = re.match(r"^\s*(\d+)", str(raw))
    return float(m.group(1)) if m else None


def _lookup_cricket(leg: dict) -> dict | None:
    league = (leg.get("league_id") or "").lower()
    base = {"ipl": "ipl", "bbl": "bbl"}.get(league)
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    # Cricket legs often store team1 as home / team2 as away in _pick
    fixtures = []
    if base:
        try:
            import cricket_fd_fixtures
            fixtures = cricket_fd_fixtures.get_competition_fixtures(base)
        except Exception:
            fixtures = []
    elif league == "hundred":
        try:
            import hundred_fixtures
            # Full season list — upcoming-only drops finished games we must grade.
            fixtures = hundred_fixtures.get_hundred_fixtures() or []
        except Exception:
            fixtures = []
    elif league == "cpl":
        try:
            import cpl_fixtures
            fixtures = cpl_fixtures.get_cpl_fixtures() or []
        except Exception:
            fixtures = []
    hits = []
    for m in fixtures:
        t1 = m.get("team1") or m.get("home_team") or ""
        t2 = m.get("team2") or m.get("away_team") or ""
        t1_elo = m.get("team1_elo_name") or t1
        t2_elo = m.get("team2_elo_name") or t2
        sides_a = (t1, t1_elo)
        sides_b = (t2, t2_elo)
        orient_ok = (
            any(_cricket_names_match(x, home) for x in sides_a)
            and any(_cricket_names_match(y, away) for y in sides_b)
        ) or (
            any(_cricket_names_match(x, away) for x in sides_a)
            and any(_cricket_names_match(y, home) for y in sides_b)
        )
        if not orient_ok:
            mu = _fold_name(leg.get("matchup") or "")
            if not (
                (_fold_name(t1) in mu or _fold_name(t1_elo) in mu)
                and (_fold_name(t2) in mu or _fold_name(t2_elo) in mu)
            ):
                continue
        hits.append(m)
    if not hits:
        return _lookup_cricket_cricsheet(leg)
    day = _kickoff_date(leg.get("kickoff_iso") or "")
    if day:
        same = [m for m in hits if (m.get("date") or "")[:10] == day]
        if same:
            hits = same
    m = next((x for x in hits if x.get("completed")), None) or hits[0]
    t1 = m.get("team1") or m.get("home_team") or ""
    t2 = m.get("team2") or m.get("away_team") or ""
    if not m.get("completed"):
        return {
            "home": t1, "away": t2,
            "home_score": m.get("score1"), "away_score": m.get("score2"),
            "completed": False,
        }
    s1 = _parse_cricket_runs(m.get("score1"))
    s2 = _parse_cricket_runs(m.get("score2"))
    winner = m.get("result") or m.get("winner")
    return {
        "home": t1,
        "away": t2,
        "home_score": s1 if s1 is not None else m.get("score1"),
        "away_score": s2 if s2 is not None else m.get("score2"),
        "completed": True,
        "winner_name": winner,
    }


def _lookup_cricket_cricsheet(leg: dict) -> dict | None:
    """Results-verification fallback when the live fixture sources above
    have no hit at all -- Cricsheet (cricsheet.org) archives lag by hours to
    days, so this only helps legs that are already stuck pending, not
    same-day settlement. See cricsheet_results.py."""
    league = (leg.get("league_id") or "").lower()
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    try:
        import cricsheet_results
        matches = cricsheet_results.get_matches(league)
    except Exception:
        return None
    hits = []
    for m in matches:
        t1, t2 = m.get("team1") or "", m.get("team2") or ""
        orient_ok = (
            (_cricket_names_match(t1, home) and _cricket_names_match(t2, away))
            or (_cricket_names_match(t1, away) and _cricket_names_match(t2, home))
        )
        if orient_ok:
            hits.append(m)
    if not hits:
        return None
    day = _kickoff_date(leg.get("kickoff_iso") or "")
    if day:
        same = [m for m in hits if (m.get("date") or "") == day]
        if same:
            hits = same
    m = hits[0]
    if not m.get("completed"):
        return None
    t1, t2 = m.get("team1") or "", m.get("team2") or ""
    return {
        "home": t1,
        "away": t2,
        "home_score": _parse_cricket_runs(m.get("score1")),
        "away_score": _parse_cricket_runs(m.get("score2")),
        "completed": True,
        "winner_name": m.get("winner_name") or m.get("result_note"),
    }


_lookup_cache: dict[str, list] = {}


def _clear_lookup_cache():
    _lookup_cache.clear()


def lookup_score_for_leg(leg: dict) -> dict | None:
    sport = (leg.get("sport_key") or "").lower()
    league = (leg.get("league_id") or "").lower()
    if sport == "soccer" or league in (
        "premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1", "mls", "primeira_liga", "liga_mx",
    ):
        return _lookup_soccer(leg)
    if league == "mlb" or (sport == "baseball" and league != "npb"):
        return _lookup_mlb(leg)
    if league == "nfl" or sport == "american_football":
        return _lookup_nfl(leg)
    if league == "npb":
        return _lookup_npb(leg)
    if sport == "cricket" or league in ("ipl", "bbl", "hundred", "mlc", "cpl"):
        return _lookup_cricket(leg)
    if sport == "tennis" or league == "atp":
        return _lookup_tennis(leg)
    return None


def _tennis_fixture_list(days_back: int = 2) -> list:
    cache_key = f"tennis:{days_back}"
    cached = _lookup_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        import tennis_fixtures
        fixtures = tennis_fixtures.get_fixtures(days_back=days_back) or []
    except Exception:
        fixtures = []
    _lookup_cache[cache_key] = fixtures
    return fixtures


def _lookup_tennis(leg: dict) -> dict | None:
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    try:
        from tennis_score import parse_tennis_score
    except Exception:
        parse_tennis_score = None
    days_back = _lookback_days_for(leg.get("kickoff_iso") or "", floor=2)
    fixtures = _tennis_fixture_list(days_back)
    kick = _kickoff_date(leg.get("kickoff_iso") or "")
    pending_hit = None
    completed_hits = []
    for m in fixtures:
        t1 = m.get("player1") or ""
        t2 = m.get("player2") or ""
        orient_ok = (
            (_names_match(t1, home) and _names_match(t2, away))
            or (_names_match(t1, away) and _names_match(t2, home))
        )
        if not orient_ok:
            mu = _fold_name(leg.get("matchup") or "")
            if _fold_name(t1) not in mu or _fold_name(t2) not in mu:
                continue
        if not m.get("completed"):
            if pending_hit is None:
                pending_hit = m
            continue
        completed_hits.append(m)
    chosen = None
    if kick:
        near = [m for m in completed_hits if _dates_within(m.get("date"), kick, 1)]
        if near:
            near.sort(key=lambda m: abs(
                (datetime.date.fromisoformat(_date_only(m.get("date")))
                 - datetime.date.fromisoformat(kick)).days
            ))
            chosen = near[0]
        elif pending_hit is not None:
            chosen = None
    elif completed_hits:
        chosen = completed_hits[0]
    if chosen is not None:
        t1 = chosen.get("player1") or ""
        t2 = chosen.get("player2") or ""
        winner = chosen.get("winner")
        raw_score = chosen.get("score")
        parsed = parse_tennis_score(raw_score) if parse_tennis_score else None
        out = {
            "home": t1,
            "away": t2,
            "home_score": None,
            "away_score": None,
            "completed": True,
            "winner_name": winner,
            "score_text": raw_score,
        }
        if parsed and not parsed.get("retired"):
            if winner and _names_match(winner, t1):
                out["home_sets"] = parsed["winner_sets"]
                out["away_sets"] = parsed["loser_sets"]
                out["home_score"] = parsed["winner_games"]
                out["away_score"] = parsed["loser_games"]
            elif winner and _names_match(winner, t2):
                out["home_sets"] = parsed["loser_sets"]
                out["away_sets"] = parsed["winner_sets"]
                out["home_score"] = parsed["loser_games"]
                out["away_score"] = parsed["winner_games"]
            else:
                out["home_score"] = parsed["winner_games"]
                out["away_score"] = parsed["loser_games"]
            out["total_sets"] = parsed["total_sets"]
            out["total_games"] = parsed["total_games"]
        elif raw_score:
            out["home_score"] = raw_score
        return out
    if pending_hit:
        return {
            "home": pending_hit.get("player1"),
            "away": pending_hit.get("player2"),
            "home_score": None,
            "away_score": None,
            "completed": False,
        }
    return _lookup_tennis_tml(leg, parse_tennis_score)


def _lookup_tennis_tml(leg: dict, parse_tennis_score) -> dict | None:
    """Results-verification fallback when ESPN/Wikipedia have no hit at all.
    TennisMyLife's ongoing_tourneys.csv (Sackmann-format columns) is already
    fetched by tennis_fixtures.py for a different purpose (identifying live
    tournaments) -- this reuses that same cached fetch rather than a new
    one. See tennis_fixtures.get_completed_matches()."""
    home = leg.get("home") or ""
    away = leg.get("away") or ""
    try:
        import tennis_fixtures
        matches = tennis_fixtures.get_completed_matches()
    except Exception:
        return None
    kick = _kickoff_date(leg.get("kickoff_iso") or "")
    hits = []
    for m in matches:
        w, l = m.get("winner_name") or "", m.get("loser_name") or ""
        if (_names_match(w, home) and _names_match(l, away)) or (
            _names_match(w, away) and _names_match(l, home)
        ):
            hits.append(m)
    if not hits:
        return None
    if kick:
        near = [m for m in hits if _dates_within(m.get("date"), kick, 2)]
        if near:
            hits = near
    m = hits[0]
    winner, loser = m.get("winner_name") or "", m.get("loser_name") or ""
    t1, t2 = (home, away) if _names_match(winner, home) else (away, home)
    raw_score = m.get("score") or ""
    out = {
        "home": t1, "away": t2,
        "home_score": None, "away_score": None,
        "completed": True,
        "winner_name": winner,
        "score_text": raw_score,
    }
    parsed = parse_tennis_score(raw_score) if parse_tennis_score and raw_score else None
    if parsed and not parsed.get("retired"):
        if t1 == winner or _names_match(t1, winner):
            out["home_score"], out["away_score"] = parsed["winner_games"], parsed["loser_games"]
        else:
            out["home_score"], out["away_score"] = parsed["loser_games"], parsed["winner_games"]
    return out


_last_public_settle_at = 0.0
PUBLIC_SETTLE_TTL_SECONDS = 60


def drop_html_result_caches() -> list[str]:
    """Delete on-disk HTML result pages so the next GET rebuilds them."""
    webapp_dir = os.path.dirname(os.path.abspath(__file__))
    dropped = []
    for name in ("mlb_results_cache.html", "results_cache.html"):
        path = os.path.join(webapp_dir, name)
        try:
            os.remove(path)
            dropped.append(name)
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception("could not drop %s", name)
    return dropped


def maybe_settle_pending(*, min_interval: int = PUBLIC_SETTLE_TTL_SECONDS) -> dict | None:
    """Settle pending slips at most once per ``min_interval`` seconds.

    Does not wipe live fixture caches first — those have their own TTLs.
    Busting on every public page made ESPN/spaia refetch and stalled the site.
    """
    import time

    global _last_public_settle_at
    now = time.time()
    if now - _last_public_settle_at < max(15, int(min_interval)):
        return None
    _last_public_settle_at = now
    return settle_pending()


def refresh_for_finished_events(*, min_interval: int = PUBLIC_SETTLE_TTL_SECONDS) -> dict | None:
    """Settle if due; only drop caches when new results actually landed."""
    stats = maybe_settle_pending(min_interval=min_interval)
    settled = 0
    if stats:
        settled = (
            int(stats.get("legs_settled") or 0)
            + int(stats.get("slips_settled") or 0)
            + int(stats.get("picks_settled") or 0)
        )
    if settled:
        try:
            import live_cache_bust
            live_cache_bust.mark_stale()
        except Exception:
            logger.exception("live_cache_bust.mark_stale failed")
        bust_live_score_caches()
        drop_html_result_caches()
    return stats


def settle_pending(*, limit_slips: int | None = None) -> dict:
    """Settle pending legs/slips and published single picks. Returns counters."""
    _clear_lookup_cache()
    init_db()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stats = {
        "legs_checked": 0,
        "legs_settled": 0,
        "slips_settled": 0,
        "picks_checked": 0,
        "picks_settled": 0,
    }
    with get_db() as conn:
        q = """
            SELECT id FROM combo_slips
            WHERE status=?
            ORDER BY slate_date DESC, id DESC
        """
        rows = conn.execute(q, (PENDING,)).fetchall()
        if limit_slips is not None:
            rows = rows[: int(limit_slips)]
        for row in rows:
            slip_id = row["id"]
            legs = conn.execute(
                "SELECT * FROM combo_legs WHERE slip_id=? ORDER BY leg_index",
                (slip_id,),
            ).fetchall()
            leg_statuses = []
            for leg in legs:
                stats["legs_checked"] += 1
                if leg["status"] != PENDING:
                    leg_statuses.append(leg["status"])
                    continue
                leg_dict = dict(leg)
                score = lookup_score_for_leg(leg_dict)
                status, note = settle_leg_against_score(
                    leg_dict.get("market") or "",
                    leg_dict.get("pick") or "",
                    score or {},
                )
                if status == PENDING:
                    age = _leg_kickoff_age_days(leg_dict.get("kickoff_iso") or "")
                    if age is not None and age >= STUCK_PENDING_VOID_DAYS:
                        # Widened lookback windows above cover normal settlement
                        # delay; a leg still unresolvable this long after kickoff
                        # is a permanent lookup failure (dropped fixture, name
                        # mismatch, unsupported league), not "not final yet" --
                        # void it rather than block the slip forever.
                        status = VOID
                        note = f"No resolvable score {age}d after kickoff -- voided"
                if status != PENDING:
                    conn.execute(
                        """
                        UPDATE combo_legs
                        SET status=?, actual_note=?, settled_at=?
                        WHERE id=?
                        """,
                        (status, note, now, leg["id"]),
                    )
                    stats["legs_settled"] += 1
                leg_statuses.append(status)
            slip_status = _roll_up_slip_status(leg_statuses)
            if slip_status != PENDING:
                conn.execute(
                    "UPDATE combo_slips SET status=?, settled_at=? WHERE id=?",
                    (slip_status, now, slip_id),
                )
                stats["slips_settled"] += 1
            else:
                # Keep slip pending but refresh if any leg moved
                conn.execute(
                    "UPDATE combo_slips SET status=? WHERE id=?",
                    (PENDING, slip_id),
                )

        pick_rows = conn.execute(
            """
            SELECT * FROM published_picks
            WHERE status=?
            ORDER BY slate_date DESC, id DESC
            """,
            (PENDING,),
        ).fetchall()
        if limit_slips is not None:
            pick_rows = pick_rows[: int(limit_slips) * 4]
        for leg in pick_rows:
            stats["picks_checked"] += 1
            leg_dict = dict(leg)
            score = lookup_score_for_leg(leg_dict)
            status, note = settle_leg_against_score(
                leg_dict.get("market") or "",
                leg_dict.get("pick") or "",
                score or {},
            )
            if status == PENDING:
                age = _leg_kickoff_age_days(leg_dict.get("kickoff_iso") or "")
                if age is not None and age >= STUCK_PENDING_VOID_DAYS:
                    status = VOID
                    note = f"No resolvable score {age}d after kickoff -- voided"
            if status != PENDING:
                conn.execute(
                    """
                    UPDATE published_picks
                    SET status=?, actual_note=?, settled_at=?
                    WHERE id=?
                    """,
                    (status, note, now, leg["id"]),
                )
                stats["picks_settled"] += 1
    return stats


def resettle_settled(*, days: int = 5) -> dict:
    """Re-grade recently settled legs if the live score now disagrees.

    Catches series-game mismatches (Tuesday's 4-1 applied to Wednesday).
    """
    _clear_lookup_cache()
    init_db()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=int(days))
    ).date().isoformat()
    stats = {
        "legs_checked": 0,
        "legs_changed": 0,
        "slips_changed": 0,
        "picks_checked": 0,
        "picks_changed": 0,
    }
    with get_db() as conn:
        legs = conn.execute(
            """
            SELECT l.* FROM combo_legs l
            JOIN combo_slips s ON s.id = l.slip_id
            WHERE s.slate_date>=? AND l.status!=?
            """,
            (cutoff, PENDING),
        ).fetchall()
        touched: set[int] = set()
        for leg in legs:
            stats["legs_checked"] += 1
            score = lookup_score_for_leg(dict(leg))
            status, note = settle_leg_against_score(
                leg["market"] or "",
                leg["pick"] or "",
                score or {},
            )
            if status == PENDING or status == leg["status"]:
                continue
            conn.execute(
                """
                UPDATE combo_legs
                SET status=?, actual_note=?, settled_at=?
                WHERE id=?
                """,
                (status, note, now, leg["id"]),
            )
            stats["legs_changed"] += 1
            touched.add(int(leg["slip_id"]))
        for slip_id in touched:
            rows = conn.execute(
                "SELECT status FROM combo_legs WHERE slip_id=?",
                (slip_id,),
            ).fetchall()
            slip_status = _roll_up_slip_status([r["status"] for r in rows])
            conn.execute(
                "UPDATE combo_slips SET status=?, settled_at=? WHERE id=?",
                (slip_status, now, slip_id),
            )
            stats["slips_changed"] += 1

        picks = conn.execute(
            """
            SELECT * FROM published_picks
            WHERE slate_date>=? AND status!=?
            """,
            (cutoff, PENDING),
        ).fetchall()
        for pick in picks:
            stats["picks_checked"] += 1
            score = lookup_score_for_leg(dict(pick))
            status, note = settle_leg_against_score(
                pick["market"] or "",
                pick["pick"] or "",
                score or {},
            )
            if status == PENDING or status == pick["status"]:
                continue
            conn.execute(
                """
                UPDATE published_picks
                SET status=?, actual_note=?, settled_at=?
                WHERE id=?
                """,
                (status, note, now, pick["id"]),
            )
            stats["picks_changed"] += 1
    return stats


def bust_live_score_caches() -> list[str]:
    """Drop in-process fixture caches so the next settle sees fresh finals."""
    cleared = ["combo_lookup"]
    _clear_lookup_cache()
    for mod_name, attr in (
        ("hundred_fixtures", "_cache"),
        ("cpl_fixtures", "_cache"),
        ("tennis_fixtures", "_cache"),
        ("npb_fixtures", "_cache"),
        ("cricket_predict", "_upcoming_cache"),
        ("cricket_fd_fixtures", "_cache"),
        ("mlb_fixtures", "_cache"),
        ("nfl_fixtures", "_cache"),
    ):
        try:
            mod = __import__(mod_name)
            cache = getattr(mod, attr, None)
            if isinstance(cache, dict):
                cache.clear()
                cleared.append(mod_name)
        except Exception:
            continue
    return cleared


def _parse_kickoff(iso_str: str) -> datetime.datetime | None:
    if not iso_str:
        return None
    raw = str(iso_str).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(raw[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    if len(raw) <= 10:
        # Date-only slates are often published a day early (tennis QFs).
        # Do not treat midnight UTC on that date as "finished".
        dt = dt + datetime.timedelta(days=1, hours=12)
    return dt


def list_overdue_pending(*, grace_hours: float = 6.0) -> list[dict]:
    """Pending legs/picks whose kickoff is older than ``grace_hours``.

    Used by the post-event timer to flag finished games the lookup still
    cannot grade (wrong feed, name mismatch, archive lag).
    """
    init_db()
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=float(grace_hours)
    )
    out = []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.sport_key, l.league_id, l.matchup, l.market,
                   l.kickoff_iso, 'leg' AS kind
            FROM combo_legs l
            WHERE l.status=?
            UNION ALL
            SELECT p.id, p.sport_key, p.league_id, p.matchup, p.market,
                   p.kickoff_iso, 'pick' AS kind
            FROM published_picks p
            WHERE p.status=?
            """,
            (PENDING, PENDING),
        ).fetchall()
        for row in rows:
            kicked = _parse_kickoff(row["kickoff_iso"] or "")
            if kicked is None or kicked > cutoff:
                continue
            out.append({
                "kind": row["kind"],
                "id": row["id"],
                "sport_key": row["sport_key"],
                "league_id": row["league_id"],
                "matchup": row["matchup"],
                "market": row["market"],
                "kickoff_iso": row["kickoff_iso"],
                "hours_overdue": round(
                    (datetime.datetime.now(datetime.timezone.utc) - kicked).total_seconds() / 3600.0,
                    1,
                ),
            })
    out.sort(key=lambda r: r["hours_overdue"], reverse=True)
    return out


def set_result_blocked(kind: str, row_id: int, *, blocked: bool = True) -> dict:
    """Block or unblock a channel slip from RESULT posting."""
    init_db()
    kind = (kind or "").strip().lower()
    table = "combo_slips" if kind == "combo" else "published_picks"
    if kind not in ("combo", "single", "pick"):
        return {"ok": False, "error": "kind"}
    if kind == "pick":
        table = "published_picks"
    try:
        row_id = int(row_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "id"}
    if row_id < 1:
        return {"ok": False, "error": "id"}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (row_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}
        conn.execute(
            f"UPDATE {table} SET result_blocked_at=? WHERE id=?",
            (now if blocked else None, row_id),
        )
    return {"ok": True, "kind": "combo" if table == "combo_slips" else "single", "id": row_id, "blocked": blocked}


def _lifecycle_phase_for_leg(leg: dict, *, settle_status: str = "") -> str:
    """upcoming | live | final — all sports."""
    st = (settle_status or leg.get("status") or "").strip().lower()
    if st in (WON, LOST, VOID, PUSH, "final"):
        return "final"
    if st == "live" or leg.get("is_live"):
        return "live"
    try:
        import today_picks
        if today_picks.fixture_is_finished(leg, sport_key=leg.get("sport_key") or ""):
            return "final"
        if (leg.get("status") or "").strip().lower() == "live":
            return "live"
    except Exception:
        kicked = _parse_kickoff(leg.get("kickoff_iso") or "")
        if kicked is not None:
            age_h = (
                datetime.datetime.now(datetime.timezone.utc) - kicked
            ).total_seconds() / 3600.0
            if age_h >= 3.5:
                return "final"
    return "upcoming"


def _lifecycle_fixture_key(sport: str, league: str, matchup: str, kickoff: str) -> str:
    return "|".join([
        (sport or "").strip().lower(),
        (league or "").strip().lower(),
        (matchup or "").strip().lower(),
        (kickoff or "")[:16],
    ])


def build_lifecycle_board(
    *,
    board_legs: list[dict] | None = None,
    sport: str | None = None,
    league: str | None = None,
    phase: str | None = None,
    settle_status: str | None = None,
    overdue_only: bool = False,
    waiting_result: bool = False,
    channel_only: bool = False,
    board_only: bool = False,
    days: int = 14,
    limit: int = 150,
) -> dict:
    """Unified upcoming→settled work queue across all sports.

    Merges Telegram channel slips, overdue pending board rows, and today's
    live board tips that have not been sent yet.
    """
    init_db()
    try:
        import today_picks
        format_kickoff = today_picks.format_kickoff_ist
    except Exception:
        def format_kickoff(iso_str, **_kw):
            return (iso_str or "")[:16]

    sport_f = (sport or "").strip().lower() or None
    league_f = (league or "").strip().lower() or None
    phase_f = (phase or "").strip().lower() or None
    if phase_f in ("", "all"):
        phase_f = None
    settle_f = (settle_status or "").strip().lower() or None
    if settle_f in ("", "all"):
        settle_f = None

    overdue_map: dict[str, dict] = {}
    for row in list_overdue_pending(grace_hours=6.0):
        key = _lifecycle_fixture_key(
            row.get("sport_key") or "",
            row.get("league_id") or "",
            row.get("matchup") or "",
            row.get("kickoff_iso") or "",
        )
        overdue_map[key] = row

    waiting_keys: set[str] = set()
    for item in list_telegram_results_due(limit=100, channel="any"):
        waiting_keys.add(f"{item.get('kind')}:{item.get('id')}")

    rows: list[dict] = []
    seen_channel: set[str] = set()
    covered_fixtures: set[str] = set()

    items = list_degen_team_items(days=days, limit=limit)
    for slip in items.get("combos") or []:
        legs = [lg for lg in (slip.get("legs") or []) if isinstance(lg, dict)]
        primary = legs[0] if legs else {}
        fx_key = _lifecycle_fixture_key(
            primary.get("sport_key") or "",
            primary.get("league_id") or "",
            primary.get("matchup") or "",
            primary.get("kickoff_iso") or "",
        )
        covered_fixtures.add(fx_key)
        chan_key = f"combo:{slip.get('id')}"
        seen_channel.add(chan_key)
        tip_bits = []
        for lg in legs[:4]:
            tip_bits.append(f"{lg.get('pick') or '—'} ({lg.get('market') or '—'})")
        od = overdue_map.get(fx_key)
        phase_guess = _lifecycle_phase_for_leg(primary, settle_status=slip.get("status") or "")
        if (slip.get("status") or "") == PENDING and od:
            phase_guess = "final"
        rows.append({
            "row_kind": "channel",
            "kind": "combo",
            "id": slip.get("id"),
            "sport_key": primary.get("sport_key") or "",
            "league_id": primary.get("league_id") or "",
            "league_label": primary.get("league_label") or primary.get("league_id") or "",
            "matchup": primary.get("matchup") or slip.get("title") or "",
            "kickoff_iso": primary.get("kickoff_iso") or "",
            "kickoff_ist": format_kickoff(primary.get("kickoff_iso") or ""),
            "phase": phase_guess,
            "tip": " · ".join(tip_bits) if tip_bits else (slip.get("title") or f"{slip.get('size')}-leg"),
            "confidence": slip.get("joint_prob"),
            "channel_serial": slip.get("telegram_serial"),
            "channel_sent": True,
            "settle_status": slip.get("status") or PENDING,
            "hours_overdue": (od or {}).get("hours_overdue"),
            "waiting_tg": bool(
                slip.get("status") in SETTLE_STATUSES
                and parse_telegram_message_ids(slip.get("telegram_message_ids"))
                and not (slip.get("telegram_result_posted_at") or "").strip()
                and not (slip.get("result_blocked_at") or "").strip()
            ),
            "waiting_x": bool(
                slip.get("status") in SETTLE_STATUSES
                and parse_telegram_message_ids(slip.get("telegram_message_ids"))
                and not (slip.get("twitter_result_posted_at") or "").strip()
                and not (slip.get("result_blocked_at") or "").strip()
            ),
            "result_blocked": bool((slip.get("result_blocked_at") or "").strip()),
            "tg_result_posted": bool((slip.get("telegram_result_posted_at") or "").strip()),
            "x_result_posted": bool((slip.get("twitter_result_posted_at") or "").strip()),
            "actual_note": " · ".join(
                (lg.get("actual_note") or "").strip()
                for lg in legs if (lg.get("actual_note") or "").strip()
            )[:180],
            "url": primary.get("url") or "",
            "result_key": f"combo:{slip.get('id')}",
            "slate_date": slip.get("slate_date") or "",
        })

    for pick in items.get("singles") or []:
        fx_key = _lifecycle_fixture_key(
            pick.get("sport_key") or "",
            pick.get("league_id") or "",
            pick.get("matchup") or "",
            pick.get("kickoff_iso") or "",
        )
        covered_fixtures.add(fx_key)
        seen_channel.add(f"single:{pick.get('id')}")
        od = overdue_map.get(fx_key)
        phase_guess = _lifecycle_phase_for_leg(pick, settle_status=pick.get("status") or "")
        if (pick.get("status") or "") == PENDING and od:
            phase_guess = "final"
        conf = pick.get("confidence")
        tip = f"{pick.get('pick') or '—'} ({pick.get('market') or '—'})"
        if conf is not None:
            try:
                tip = f"{tip} · {round(float(conf) * 100)}%"
            except (TypeError, ValueError):
                pass
        rows.append({
            "row_kind": "channel",
            "kind": "single",
            "id": pick.get("id"),
            "sport_key": pick.get("sport_key") or "",
            "league_id": pick.get("league_id") or "",
            "league_label": pick.get("league_label") or pick.get("league_id") or "",
            "matchup": pick.get("matchup") or "",
            "kickoff_iso": pick.get("kickoff_iso") or "",
            "kickoff_ist": format_kickoff(pick.get("kickoff_iso") or ""),
            "phase": phase_guess,
            "tip": tip,
            "confidence": conf,
            "channel_serial": pick.get("telegram_serial"),
            "channel_sent": True,
            "settle_status": pick.get("status") or PENDING,
            "hours_overdue": (od or {}).get("hours_overdue"),
            "waiting_tg": bool(
                pick.get("status") in SETTLE_STATUSES
                and parse_telegram_message_ids(pick.get("telegram_message_ids"))
                and not (pick.get("telegram_result_posted_at") or "").strip()
                and not (pick.get("result_blocked_at") or "").strip()
            ),
            "waiting_x": bool(
                pick.get("status") in SETTLE_STATUSES
                and parse_telegram_message_ids(pick.get("telegram_message_ids"))
                and not (pick.get("twitter_result_posted_at") or "").strip()
                and not (pick.get("result_blocked_at") or "").strip()
            ),
            "result_blocked": bool((pick.get("result_blocked_at") or "").strip()),
            "tg_result_posted": bool((pick.get("telegram_result_posted_at") or "").strip()),
            "x_result_posted": bool((pick.get("twitter_result_posted_at") or "").strip()),
            "actual_note": (pick.get("actual_note") or "")[:180],
            "url": pick.get("url") or "",
            "result_key": f"single:{pick.get('id')}",
            "slate_date": pick.get("slate_date") or "",
        })

    # Today's board tips not yet on the channel (all sports).
    for leg in board_legs or []:
        if not isinstance(leg, dict):
            continue
        fx_key = _lifecycle_fixture_key(
            leg.get("sport_key") or "",
            leg.get("league_id") or "",
            leg.get("matchup") or "",
            leg.get("kickoff_iso") or leg.get("date") or "",
        )
        if fx_key in covered_fixtures:
            continue
        phase_guess = (leg.get("status") or "").strip().lower()
        if phase_guess not in ("upcoming", "live", "final"):
            phase_guess = _lifecycle_phase_for_leg(leg)
        if phase_guess == "final":
            continue  # finished unsent tips clutter less; overdue board covers settle gaps
        conf = leg.get("confidence")
        tip = f"{leg.get('pick') or '—'} ({leg.get('market') or '—'})"
        if conf is not None:
            try:
                tip = f"{tip} · {round(float(conf) * 100)}%"
            except (TypeError, ValueError):
                pass
        covered_fixtures.add(fx_key)
        rows.append({
            "row_kind": "board",
            "kind": "",
            "id": None,
            "sport_key": leg.get("sport_key") or "",
            "league_id": leg.get("league_id") or "",
            "league_label": leg.get("league_label") or leg.get("league_id") or "",
            "matchup": leg.get("matchup") or "",
            "kickoff_iso": leg.get("kickoff_iso") or leg.get("date") or "",
            "kickoff_ist": format_kickoff(leg.get("kickoff_iso") or leg.get("date") or "")
            or (leg.get("when") or ""),
            "phase": phase_guess,
            "tip": tip,
            "confidence": conf,
            "channel_serial": None,
            "channel_sent": False,
            "settle_status": "",
            "hours_overdue": None,
            "waiting_tg": False,
            "waiting_x": False,
            "result_blocked": False,
            "tg_result_posted": False,
            "x_result_posted": False,
            "actual_note": "",
            "url": leg.get("url") or "",
            "result_key": "",
            "slate_date": "",
        })

    # Overdue pending that never appeared above (board ledger gaps).
    for key, od in overdue_map.items():
        if key in covered_fixtures:
            continue
        rows.append({
            "row_kind": "overdue",
            "kind": od.get("kind") or "pick",
            "id": od.get("id"),
            "sport_key": od.get("sport_key") or "",
            "league_id": od.get("league_id") or "",
            "league_label": od.get("league_id") or "",
            "matchup": od.get("matchup") or "",
            "kickoff_iso": od.get("kickoff_iso") or "",
            "kickoff_ist": format_kickoff(od.get("kickoff_iso") or ""),
            "phase": "final",
            "tip": od.get("market") or "—",
            "confidence": None,
            "channel_serial": None,
            "channel_sent": False,
            "settle_status": PENDING,
            "hours_overdue": od.get("hours_overdue"),
            "waiting_tg": False,
            "waiting_x": False,
            "result_blocked": False,
            "tg_result_posted": False,
            "x_result_posted": False,
            "actual_note": "overdue pending",
            "url": "",
            "result_key": "",
            "slate_date": "",
        })

    def _match(row: dict) -> bool:
        if sport_f and (row.get("sport_key") or "").strip().lower() != sport_f:
            return False
        if league_f and (row.get("league_id") or "").strip().lower() != league_f:
            return False
        if phase_f and (row.get("phase") or "") != phase_f:
            return False
        if settle_f:
            if settle_f == "pending" and (row.get("settle_status") or PENDING) != PENDING:
                if row.get("settle_status"):
                    return False
            elif settle_f != "pending" and (row.get("settle_status") or "") != settle_f:
                return False
        if overdue_only and not row.get("hours_overdue"):
            return False
        if waiting_result and not (row.get("waiting_tg") or row.get("waiting_x")):
            return False
        if channel_only and not row.get("channel_sent"):
            return False
        if board_only and row.get("channel_sent"):
            return False
        return True

    filtered = [r for r in rows if _match(r)]
    phase_rank = {"live": 0, "final": 1, "upcoming": 2}
    filtered.sort(
        key=lambda r: (
            0 if r.get("hours_overdue") else 1,
            0 if (r.get("waiting_tg") or r.get("waiting_x")) else 1,
            phase_rank.get(r.get("phase") or "", 9),
            r.get("kickoff_iso") or "",
        )
    )
    filtered = filtered[: max(1, min(int(limit), 300))]

    sports = sorted({(r.get("sport_key") or "").strip() for r in rows if r.get("sport_key")})
    leagues = sorted({
        ((r.get("league_id") or "").strip(), (r.get("league_label") or r.get("league_id") or "").strip())
        for r in rows if r.get("league_id")
    }, key=lambda x: (x[1] or "").lower())

    kpis = {
        "total": len(filtered),
        "upcoming": sum(1 for r in filtered if r.get("phase") == "upcoming"),
        "live": sum(1 for r in filtered if r.get("phase") == "live"),
        "final": sum(1 for r in filtered if r.get("phase") == "final"),
        "overdue": sum(1 for r in filtered if r.get("hours_overdue")),
        "waiting_tg": sum(1 for r in filtered if r.get("waiting_tg")),
        "waiting_x": sum(1 for r in filtered if r.get("waiting_x")),
        "channel_sent": sum(1 for r in filtered if r.get("channel_sent")),
        "board_only": sum(1 for r in filtered if not r.get("channel_sent")),
        "pending": sum(1 for r in filtered if (r.get("settle_status") or "") == PENDING),
    }
    return {
        "rows": filtered,
        "kpis": kpis,
        "sports": sports,
        "leagues": leagues,
        "waiting_due_count": len(waiting_keys),
    }


def _conf_band(conf: float | None) -> str:
    if conf is None:
        return "unknown"
    c = float(conf)
    if c < 0.60:
        return "0.50-0.60"
    if c < 0.68:
        return "0.60-0.68"
    if c < 0.72:
        return "0.68-0.72"
    if c < 0.78:
        return "0.72-0.78"
    return "0.78+"


def _brier(conf: float | None, won: bool) -> float | None:
    if conf is None:
        return None
    p = min(1.0, max(0.0, float(conf)))
    y = 1.0 if won else 0.0
    return (p - y) ** 2


def _agg_bucket() -> dict:
    return {"n": 0, "won": 0, "lost": 0, "brier_sum": 0.0, "brier_n": 0}


def _finalize_bucket(b: dict) -> dict:
    n = int(b["n"])
    won = int(b["won"])
    lost = int(b["lost"])
    decided = won + lost
    brier_n = int(b["brier_n"])
    return {
        "n": n,
        "won": won,
        "lost": lost,
        "hit_rate": (won / decided) if decided else None,
        "brier": (b["brier_sum"] / brier_n) if brier_n else None,
    }


def feedback_report(*, days: int = 14, target_hit_rate: float = 0.70) -> dict:
    """Rolling settled-pick feedback for EC2 (no model reload, SQLite only)."""
    init_db()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).date().isoformat()
    overall = _agg_bucket()
    by_league: dict[str, dict] = {}
    by_market: dict[str, dict] = {}
    by_band: dict[str, dict] = {}

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT league_id, market, confidence, status
            FROM published_picks
            WHERE slate_date>=? AND status IN (?, ?)
              AND IFNULL(source, '') != ?
            """,
            (cutoff, WON, LOST, SOURCE_DEGEN_TEAM),
        ).fetchall()
        # Also include combo legs (published slips) so older snapshots count.
        leg_rows = conn.execute(
            """
            SELECT l.league_id, l.market, l.confidence, l.status
            FROM combo_legs l
            JOIN combo_slips s ON s.id = l.slip_id
            WHERE s.slate_date>=? AND l.status IN (?, ?)
              AND IFNULL(s.source, '') != ?
            """,
            (cutoff, WON, LOST, SOURCE_DEGEN_TEAM),
        ).fetchall()

    for row in list(rows) + list(leg_rows):
        status = row["status"]
        won = status == WON
        conf = row["confidence"]
        league = (row["league_id"] or "unknown").strip() or "unknown"
        market = (row["market"] or "unknown").strip() or "unknown"
        band = _conf_band(conf)

        for key, store in (
            (None, None),
            (league, by_league),
            (market, by_market),
            (band, by_band),
        ):
            if store is None:
                b = overall
            else:
                b = store.setdefault(key, _agg_bucket())
            b["n"] += 1
            if won:
                b["won"] += 1
            else:
                b["lost"] += 1
            br = _brier(conf, won)
            if br is not None:
                b["brier_sum"] += br
                b["brier_n"] += 1

    overall_f = _finalize_bucket(overall)
    decided = (overall_f["won"] or 0) + (overall_f["lost"] or 0)
    suggested = None
    # Only raise the live floor when we have enough settled picks and are missing target.
    if decided >= 30 and overall_f["hit_rate"] is not None:
        if overall_f["hit_rate"] < (target_hit_rate - 0.02):
            suggested = 0.72
        elif overall_f["hit_rate"] < target_hit_rate:
            suggested = 0.70

    return {
        "days": days,
        "target_hit_rate": target_hit_rate,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "overall": overall_f,
        "by_league": {k: _finalize_bucket(v) for k, v in sorted(by_league.items())},
        "by_market": {k: _finalize_bucket(v) for k, v in sorted(by_market.items())},
        "by_conf_band": {k: _finalize_bucket(v) for k, v in sorted(by_band.items())},
        "suggested_min_confidence": suggested,
        "promote_baseline": {
            "hit_rate": overall_f.get("hit_rate"),
            "n": decided,
            "brier": overall_f.get("brier"),
            "days": days,
        },
        "slips": summary(days=days),
    }


def summary(
    days: int = 30,
    *,
    source: str | None = None,
    exclude_source: str | None = SOURCE_DEGEN_TEAM,
) -> dict:
    init_db()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).date().isoformat()
    with get_db() as conn:
        params: list[Any] = [cutoff]
        q = "SELECT status, COUNT(*) AS n FROM combo_slips WHERE slate_date>=?"
        if source:
            q += " AND source=?"
            params.append(source)
        elif exclude_source:
            q += " AND IFNULL(source, '') != ?"
            params.append(exclude_source)
        q += " GROUP BY status"
        rows = conn.execute(q, params).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    won = counts.get(WON, 0)
    lost = counts.get(LOST, 0)
    pending = counts.get(PENDING, 0)
    void = counts.get(VOID, 0) + counts.get(PUSH, 0)
    decided = won + lost
    return {
        "days": days,
        "source": source or "all",
        "won": won,
        "lost": lost,
        "pending": pending,
        "void": void,
        "decided": decided,
        "hit_rate": (won / decided) if decided else None,
        "total": won + lost + pending + void,
    }


def list_slips(
    *,
    days: int = 30,
    status: str | None = None,
    limit: int = 100,
    source: str | None = None,
    exclude_source: str | None = SOURCE_DEGEN_TEAM,
) -> list[dict]:
    init_db()
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).date().isoformat()
    with get_db() as conn:
        params: list[Any] = [cutoff]
        q = "SELECT * FROM combo_slips WHERE slate_date>=?"
        if status:
            q += " AND status=?"
            params.append(status)
        if source:
            q += " AND source=?"
            params.append(source)
        elif exclude_source:
            q += " AND IFNULL(source, '') != ?"
            params.append(exclude_source)
        q += " ORDER BY slate_date DESC, id DESC LIMIT ?"
        params.append(int(limit))
        slips = [dict(r) for r in conn.execute(q, params).fetchall()]
        out = []
        for slip in slips:
            legs = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM combo_legs WHERE slip_id=? ORDER BY leg_index",
                    (slip["id"],),
                ).fetchall()
            ]
            slip["legs"] = legs
            out.append(slip)
    return out
