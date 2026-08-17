"""Lightweight, dependency-free site-traffic logging for the admin
dashboard: one row per page request (timestamp, IP, path, method, user
agent, referrer), stored in its own SQLite file so it never touches
users.db. No third-party analytics service, no cookies, no client-side JS
-- purely server-side request logging, queried by app.py's /admin routes.
"""
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics.db")

# Requests to these path prefixes are noise for a traffic dashboard (static
# assets, the dashboard's own polling, health checks) -- excluded so the
# numbers reflect real visitor activity, not asset fetches.
EXCLUDED_PREFIXES = ("/static/", "/admin", "/favicon.ico", "/healthz")


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
    """Best-effort traffic log. Never raises — a bad/locked DB must not 500 the site."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO visits (ts, ip, path, method, status, user_agent, referrer) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), ip, path, method, status,
                 (user_agent or "")[:500], (referrer or "")[:500]),
            )
    except Exception:
        # Readonly dir after Windows tar deploy, disk full, etc.
        return


def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- bot detection

# Case-insensitive substrings that show up in known crawlers, link-preview
# fetchers, scripted HTTP clients, and monitoring probes -- not in real
# browsers. Substring matching (not full UA parsing) deliberately errs
# toward catching more bots; there's no session/account system here to
# classify visitors more precisely without cookies.
BOT_UA_SUBSTRINGS = (
    "bot", "spider", "crawl", "slurp",                                    # search/SEO crawlers
    "facebookexternalhit", "whatsapp", "telegram",                        # link-preview fetchers
    "curl", "wget", "python-requests", "python-urllib", "go-http-client",
    "libwww-perl", "scrapy", "okhttp", "postmanruntime",                  # scripted HTTP clients
    "headlesschrome", "phantomjs", "puppeteer", "playwright",             # headless browsers
    "pingdom", "uptimerobot", "gtmetrix", "lighthouse", "pagespeed",      # monitoring/audit tools
)


def is_bot_user_agent(user_agent):
    """True for known crawlers/scripted clients/monitoring probes, and for
    a missing or blank UA -- no real browser sends an empty one."""
    ua = (user_agent or "").strip().lower()
    if not ua:
        return True
    return any(s in ua for s in BOT_UA_SUBSTRINGS)


def _bot_sql_clause():
    """SQL equivalent of is_bot_user_agent(), so aggregate human/bot counts
    can be computed by SQLite instead of pulling every row into Python.
    Returns (sql_fragment_with_placeholders, params_in_order)."""
    ors = " OR ".join(["LOWER(user_agent) LIKE ?"] * len(BOT_UA_SUBSTRINGS))
    clause = f"(user_agent IS NULL OR user_agent = '' OR {ors})"
    params = [f"%{s}%" for s in BOT_UA_SUBSTRINGS]
    return clause, params


def summary(days=30):
    bot_clause, bot_params = _bot_sql_clause()
    with get_db() as conn:
        since = _since(days)
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM visits WHERE ts >= ?", (since,),
        ).fetchone()["n"]
        bot_total = conn.execute(
            f"SELECT COUNT(*) AS n FROM visits WHERE ts >= ? AND {bot_clause}",
            (since, *bot_params),
        ).fetchone()["n"]
        unique_visitors = conn.execute(
            "SELECT COUNT(DISTINCT ip) AS n FROM visits WHERE ts >= ?", (since,),
        ).fetchone()["n"]
        today_since = _since(1)
        today_total = conn.execute(
            "SELECT COUNT(*) AS n FROM visits WHERE ts >= ?", (today_since,),
        ).fetchone()["n"]
        today_bot_total = conn.execute(
            f"SELECT COUNT(*) AS n FROM visits WHERE ts >= ? AND {bot_clause}",
            (today_since, *bot_params),
        ).fetchone()["n"]
        today_unique = conn.execute(
            "SELECT COUNT(DISTINCT ip) AS n FROM visits WHERE ts >= ?", (today_since,),
        ).fetchone()["n"]
    return {
        "total_visits": total, "unique_visitors": unique_visitors,
        "human_visits": total - bot_total, "bot_visits": bot_total,
        "today_visits": today_total, "today_unique_visitors": today_unique,
        "today_human_visits": today_total - today_bot_total, "today_bot_visits": today_bot_total,
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


def _bot_having_clause(filter):
    """HAVING fragment for filter in {"all","human","bot"}, to apply after
    grouping by IP -- "" for "all" (no extra filtering)."""
    if filter == "bot":
        return "HAVING bot_hits > 0"
    if filter == "human":
        return "HAVING bot_hits = 0"
    return ""


def top_visitors(days=30, limit=25, filter="all"):
    """Distinct IPs ranked by request volume, with their most recent visit
    and how many different pages they've hit -- the closest thing to a
    "who's actually browsing the site" view without cookies/accounts.
    `filter` narrows to "human" or "bot" IPs (any bot-signature hit tags
    the whole IP as a bot -- see BOT_UA_SUBSTRINGS)."""
    bot_clause, bot_params = _bot_sql_clause()
    having = _bot_having_clause(filter)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT ip, COUNT(*) AS visits, COUNT(DISTINCT path) AS distinct_pages, "
            f"MAX(ts) AS last_seen, MIN(ts) AS first_seen, "
            f"SUM(CASE WHEN {bot_clause} THEN 1 ELSE 0 END) AS bot_hits "
            f"FROM visits WHERE ts >= ? GROUP BY ip {having} ORDER BY visits DESC LIMIT ?",
            (*bot_params, _since(days), limit),
        ).fetchall()
    result = [dict(r) for r in rows]
    for r in result:
        r["is_bot"] = r.pop("bot_hits") > 0
    return result


def recurring_visitors(days=30, min_days=2, limit=25, filter="all"):
    """IPs seen on at least `min_days` distinct calendar days within the
    window -- the closest signal to "repeat/returning visitors" without
    cookies or accounts. A bot crawling on a schedule shows up here too,
    which is exactly why the human/bot filter matters for this list."""
    bot_clause, bot_params = _bot_sql_clause()
    having_bot = _bot_having_clause(filter)
    having_bot = f"AND {having_bot[len('HAVING '):]}" if having_bot else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT ip, COUNT(*) AS visits, COUNT(DISTINCT substr(ts,1,10)) AS distinct_days, "
            f"COUNT(DISTINCT path) AS distinct_pages, MAX(ts) AS last_seen, MIN(ts) AS first_seen, "
            f"SUM(CASE WHEN {bot_clause} THEN 1 ELSE 0 END) AS bot_hits "
            f"FROM visits WHERE ts >= ? GROUP BY ip "
            f"HAVING distinct_days >= ? {having_bot} "
            f"ORDER BY distinct_days DESC, visits DESC LIMIT ?",
            (*bot_params, _since(days), min_days, limit),
        ).fetchall()
    result = [dict(r) for r in rows]
    for r in result:
        r["is_bot"] = r.pop("bot_hits") > 0
    return result


def recent_visits(limit=100, filter="all"):
    bot_clause, bot_params = _bot_sql_clause()
    where = ""
    params = []
    if filter == "bot":
        where, params = f"WHERE {bot_clause}", bot_params
    elif filter == "human":
        where, params = f"WHERE NOT {bot_clause}", bot_params
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT ts, ip, path, method, status, user_agent, referrer "
            f"FROM visits {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    result = [dict(r) for r in rows]
    for r in result:
        r["is_bot"] = is_bot_user_agent(r.get("user_agent"))
    return result


# ---- Get Prediction clicks (human fixture unlocks) ----------------------

_GP_API_RE = re.compile(
    r"^/(premier_league|la_liga|serie_a|bundesliga|ligue_1|mls|primeira_liga|liga_mx|mlb|npb|nfl)"
    r"/api/predict(?:/|$|\?)",
)
_GP_GO_RE = re.compile(
    r"^/go/gp/(ipl|cpl|hundred|mlc|bbl|tennis)/fixture/",
)
_GP_LEAGUE_LABELS = {
    "premier_league": "Premier League",
    "la_liga": "La Liga",
    "serie_a": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue_1": "Ligue 1",
    "mls": "MLS",
    "primeira_liga": "Primeira Liga",
    "liga_mx": "Liga MX",
    "mlb": "MLB",
    "npb": "NPB",
    "nfl": "NFL",
    "ipl": "IPL",
    "cpl": "CPL",
    "hundred": "The Hundred",
    "mlc": "MLC",
    "bbl": "BBL",
    "tennis": "ATP",
}
_GP_PATH_SQL = (
    "(path LIKE '%/api/predict/%' OR path LIKE '%/api/predict?%' "
    "OR path = '/mlb/api/predict' OR path = '/npb/api/predict' "
    "OR path = '/nfl/api/predict' "
    "OR path LIKE '/go/gp/%/fixture/%' "
    "OR path LIKE '/api/unlock/%')"
)


def is_get_prediction_path(path):
    """True for Get Prediction unlocks, cricket/tennis hops, and slate unlocks."""
    raw = (path or "").split("?", 1)[0]
    if raw.startswith("/api/unlock/"):
        return True
    if _GP_GO_RE.match(raw):
        return True
    return bool(_GP_API_RE.match(path or raw))


def is_get_prediction_api_path(path):
    """Predict API or slate unlock — attach query string when logging."""
    raw = (path or "").split("?", 1)[0]
    return bool(_GP_API_RE.match(path or "")) or raw.startswith("/api/unlock/")


def get_prediction_event_meta(path):
    """League key + human label for a logged Get Prediction path."""
    raw, _, qs = (path or "").partition("?")
    parts = [p for p in raw.strip("/").split("/") if p]
    league = ""
    label = "Unknown fixture"
    if len(parts) >= 2 and parts[0] == "api" and parts[1] == "unlock":
        kind = parts[2] if len(parts) > 2 else ""
        q = parse_qs(qs)
        event = unquote((q.get("event") or [""])[0]).strip() or kind
        if kind == "combo":
            return {
                "kind": "combo",
                "league": "combo",
                "league_label": "Best Combos",
                "label": event or "Unlock combo",
            }
        if kind == "pick":
            return {
                "kind": "pick",
                "league": "pick",
                "league_label": "Today's Picks",
                "label": event or "Unlock prediction",
            }
        return {
            "kind": "unlock",
            "league": kind or "unlock",
            "league_label": "Unlock",
            "label": event or "Unlock prediction",
        }
    if len(parts) >= 5 and parts[0] == "go" and parts[1] == "gp":
        league = parts[2]
        slug = unquote(parts[4]).replace("-", " ")
        label = slug
    elif "api" in parts and "predict" in parts:
        league = parts[0]
        idx = parts.index("predict")
        teams = [unquote(p) for p in parts[idx + 1:idx + 3] if p]
        if teams:
            label = " vs ".join(teams)
        elif qs:
            q = parse_qs(qs)
            home = (q.get("home") or q.get("home_name") or [""])[0]
            away = (q.get("away") or q.get("away_name") or [""])[0]
            if home or away:
                label = f"{unquote(away)} vs {unquote(home)}".strip()
            else:
                label = _GP_LEAGUE_LABELS.get(league, league or "Fixture")
        else:
            label = _GP_LEAGUE_LABELS.get(league, league or "Fixture")
    league_label = _GP_LEAGUE_LABELS.get(league, league.replace("_", " ").title() if league else "—")
    return {
        "kind": "event",
        "league": league,
        "league_label": league_label,
        "label": label or "Unknown fixture",
    }


def _empty_gp_stats(days=30):
    return {
        "clicks": 0,
        "today_clicks": 0,
        "unique_events": 0,
        "today_unique_events": 0,
        "unique_humans": 0,
        "today_unique_humans": 0,
        "events": [],
        "window_days": days,
        "fixture_clicks": 0,
        "combo_clicks": 0,
        "pick_clicks": 0,
        "today_fixture_clicks": 0,
        "today_combo_clicks": 0,
        "today_pick_clicks": 0,
    }


def get_prediction_stats(days=30, limit=50):
    """Human Unlock prediction clicks, broken down by fixture/event.

    Bots are excluded. Soccer/MLB/NPB come from `/api/predict`; cricket and
    tennis from `/go/gp/...` button hops; Today's Picks / combos from `/api/unlock`.
    """
    bot_clause, bot_params = _bot_sql_clause()
    human = f"NOT {bot_clause}"
    out = _empty_gp_stats(days)
    try:
        with get_db() as conn:
            since = _since(days)
            today_since = _since(1)

            def _count(since_ts):
                row = conn.execute(
                    f"SELECT COUNT(*) AS clicks, COUNT(DISTINCT path) AS events, "
                    f"COUNT(DISTINCT ip) AS humans FROM visits "
                    f"WHERE ts >= ? AND { _GP_PATH_SQL } AND {human}",
                    (since_ts, *bot_params),
                ).fetchone()
                return int(row["clicks"] or 0), int(row["events"] or 0), int(row["humans"] or 0)

            out["clicks"], out["unique_events"], out["unique_humans"] = _count(since)
            out["today_clicks"], out["today_unique_events"], out["today_unique_humans"] = _count(today_since)

            def _kind_count(since_ts, like):
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM visits "
                    f"WHERE ts >= ? AND path LIKE ? AND {human}",
                    (since_ts, like, *bot_params),
                ).fetchone()
                return int(row["n"] or 0)

            out["combo_clicks"] = _kind_count(since, "/api/unlock/combo%")
            out["pick_clicks"] = _kind_count(since, "/api/unlock/pick%")
            out["today_combo_clicks"] = _kind_count(today_since, "/api/unlock/combo%")
            out["today_pick_clicks"] = _kind_count(today_since, "/api/unlock/pick%")
            out["fixture_clicks"] = max(0, out["clicks"] - out["combo_clicks"] - out["pick_clicks"])
            out["today_fixture_clicks"] = max(
                0, out["today_clicks"] - out["today_combo_clicks"] - out["today_pick_clicks"],
            )
            rows = conn.execute(
                f"SELECT path, COUNT(*) AS clicks, COUNT(DISTINCT ip) AS unique_humans, "
                f"MAX(ts) AS last_seen FROM visits "
                f"WHERE ts >= ? AND { _GP_PATH_SQL } AND {human} "
                f"GROUP BY path ORDER BY clicks DESC, last_seen DESC LIMIT ?",
                (since, *bot_params, int(limit)),
            ).fetchall()
        events = []
        for r in rows:
            meta = get_prediction_event_meta(r["path"])
            events.append({
                "path": r["path"],
                "clicks": r["clicks"],
                "unique_humans": r["unique_humans"],
                "last_seen": r["last_seen"],
                "kind_label": {
                    "event": "Sports event",
                    "pick": "Today's Picks",
                    "combo": "Best Combos",
                }.get(meta.get("kind"), "Unlock"),
                **meta,
            })
        out["events"] = events
    except Exception:
        return _empty_gp_stats(days)
    return out
