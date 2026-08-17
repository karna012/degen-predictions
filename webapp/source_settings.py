"""Admin-editable data sources, result sources, Telegram, and API keys.

Stored at ``data/admin/source_settings.json``. Env vars remain the fallback
for secrets so production.env still works; admin values override when set.

Consumers should call ``telegram_credentials()``, ``api_key()``, and
``feed_preference(sport)`` rather than reading env directly.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO / "data" / "admin" / "source_settings.json"
SECRET_PLACEHOLDER = "••••••••"
KEEP_SECRET = "__keep__"

# Catalog shown in admin + validated on save.
FEED_CATALOG: dict[str, dict[str, Any]] = {
    "mlb": {
        "label": "MLB",
        "sport": "baseball",
        "fixture_options": [
            ("mlb_statsapi", "MLB Stats API (official, free)"),
            ("espn_cdn", "ESPN CDN scoreboard"),
            ("espn_site", "ESPN site.api (often 403 on cloud)"),
        ],
        "result_options": [
            ("mlb_statsapi", "MLB Stats API"),
            ("espn_cdn", "ESPN CDN"),
            ("retrosheet", "Retrosheet (history only)"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "mlb_statsapi",
            "fixtures_fallback": "espn_cdn",
            "results_primary": "mlb_statsapi",
            "results_fallback": "espn_cdn",
        },
    },
    "nfl": {
        "label": "NFL",
        "sport": "football",
        "fixture_options": [
            ("espn_cdn", "ESPN CDN scoreboard"),
            ("espn_site", "ESPN site.api"),
            ("nflverse", "nflverse games.csv (free, no key, regular season + playoffs only)"),
        ],
        "result_options": [
            ("espn_cdn", "ESPN CDN"),
            ("espn_site", "ESPN site.api"),
            ("nflverse", "nflverse games.csv"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "espn_cdn",
            "fixtures_fallback": "espn_site",
            "results_primary": "espn_cdn",
            "results_fallback": "espn_site",
        },
    },
    "soccer": {
        "label": "Soccer (PL / La Liga / …)",
        "sport": "soccer",
        "fixture_options": [
            ("fixturedownload", "fixturedownload.com JSON"),
            ("openfootball", "openfootball/football.json (free, no key)"),
            ("football_data_org", "football-data.org (needs token)"),
            ("api_sports", "API-Football / API-Sports (needs key)"),
        ],
        "result_options": [
            ("fixturedownload", "fixturedownload.com"),
            ("openfootball", "openfootball/football.json"),
            ("football_data_csv", "football-data.co.uk CSVs"),
            ("football_data_org", "football-data.org"),
            ("api_sports", "API-Sports"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "fixturedownload",
            "fixtures_fallback": "openfootball",
            "results_primary": "fixturedownload",
            "results_fallback": "football_data_csv",
        },
    },
    "npb": {
        "label": "NPB",
        "sport": "baseball",
        "fixture_options": [
            ("spaia", "spaia.jp schedules"),
            ("spaia_games_info", "spaia games_info_by_date"),
            ("npbjp", "npb.jp official schedule (free, no key)"),
            ("api_sports", "API-Sports Baseball (needs key)"),
        ],
        "result_options": [
            ("spaia_games_info", "spaia games_info_by_date"),
            ("spaia_schedules", "spaia season schedules (lags)"),
            ("npbjp", "npb.jp official schedule"),
            ("api_sports", "API-Sports"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "spaia",
            "fixtures_fallback": "npbjp",
            "results_primary": "spaia_games_info",
            "results_fallback": "spaia_schedules",
        },
    },
    "tennis": {
        "label": "Tennis (ATP)",
        "sport": "tennis",
        "fixture_options": [
            ("espn_web", "ESPN site.web.api"),
            ("espn_site", "ESPN site.api"),
            ("wikipedia", "Wikipedia brackets (merge)"),
            ("tennismylife", "TennisMyLife ongoing CSV"),
            ("api_sports", "API-Sports Tennis (needs key)"),
        ],
        "result_options": [
            ("espn_web", "ESPN web API"),
            ("espn_site", "ESPN site.api"),
            ("tennismylife", "TennisMyLife CSVs (history)"),
            ("api_sports", "API-Sports"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "espn_web",
            "fixtures_fallback": "wikipedia",
            "results_primary": "espn_web",
            "results_fallback": "tennismylife",
        },
    },
    "cricket": {
        "label": "Cricket (IPL / BBL / Hundred / CPL)",
        "sport": "cricket",
        "fixture_options": [
            ("fixturedownload", "fixturedownload (IPL/BBL)"),
            ("wikipedia", "Wikipedia (Hundred/CPL)"),
            ("cricsheet", "Cricsheet (history only)"),
            ("entity_sport", "Entity Sport / Roanuz (needs key)"),
            ("api_sports", "API-Sports Cricket (needs key)"),
        ],
        "result_options": [
            ("fixturedownload", "fixturedownload"),
            ("wikipedia", "Wikipedia"),
            ("cricsheet", "Cricsheet"),
            ("entity_sport", "Entity Sport"),
            ("api_sports", "API-Sports"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "fixturedownload",
            "fixtures_fallback": "wikipedia",
            "results_primary": "fixturedownload",
            "results_fallback": "wikipedia",
        },
    },
    "stocks": {
        "label": "Stocks",
        "sport": "markets",
        "kind": "markets",
        "role_labels": {
            "fixtures_primary": "Board data (main)",
            "fixtures_fallback": "Board data (backup)",
            "results_primary": "Live quotes (main)",
            "results_fallback": "Live quotes (backup)",
        },
        "fixture_options": [
            ("ranked_csv", "ranked_assets.csv (model board)"),
            ("yahoo_chart", "Yahoo Finance chart API"),
            ("fmp", "Financial Modeling Prep (needs key)"),
        ],
        "result_options": [
            ("yahoo_chart", "Yahoo Finance"),
            ("tradingview", "TradingView scanner"),
            ("fmp", "FMP (needs key)"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "ranked_csv",
            "fixtures_fallback": "yahoo_chart",
            "results_primary": "yahoo_chart",
            "results_fallback": "tradingview",
        },
    },
    "crypto": {
        "label": "Crypto",
        "sport": "markets",
        "kind": "markets",
        "role_labels": {
            "fixtures_primary": "Board data (main)",
            "fixtures_fallback": "Board data (backup)",
            "results_primary": "Live prices (main)",
            "results_fallback": "Live prices (backup)",
        },
        "fixture_options": [
            ("ranked_csv", "ranked_assets.csv (model board)"),
            ("coingecko", "CoinGecko markets API"),
            ("binance", "Binance public ticker"),
        ],
        "result_options": [
            ("coingecko", "CoinGecko"),
            ("binance", "Binance"),
            ("ranked_csv", "CSV snapshot only"),
        ],
        "defaults": {
            "enabled": True,
            "fixtures_primary": "ranked_csv",
            "fixtures_fallback": "coingecko",
            "results_primary": "coingecko",
            "results_fallback": "binance",
        },
    },
}

API_FIELDS: dict[str, dict[str, str]] = {
    "api_sports_key": {
        "label": "API-Sports / API-Football key",
        "env": "API_SPORTS_KEY",
        "hint": "x-apisports-key — soccer, NFL, baseball, tennis",
    },
    "football_data_org_token": {
        "label": "football-data.org token",
        "env": "FOOTBALL_DATA_ORG_TOKEN",
        "hint": "Optional soccer fixtures/results API",
    },
    "odds_api_key": {
        "label": "The Odds API key",
        "env": "ODDS_API_KEY",
        "hint": "Optional market odds",
    },
    "rapidapi_key": {
        "label": "RapidAPI key",
        "env": "RAPIDAPI_KEY",
        "hint": "Used by non-sports tools; kept here for convenience",
    },
    "entity_sport_token": {
        "label": "Entity Sport / cricket API token",
        "env": "ENTITY_SPORT_TOKEN",
        "hint": "When you add a cricket commercial feed",
    },
    "fmp_api_key": {
        "label": "Financial Modeling Prep key",
        "env": "FMP_API_KEY",
        "hint": "Optional stock logos / quotes",
    },
    "coingecko_api_key": {
        "label": "CoinGecko API key",
        "env": "COINGECKO_API_KEY",
        "hint": "Optional — raises rate limits for crypto logos/prices",
    },
}

# Full admin vault — every app secret / key, shown in plaintext to admins.
SECRET_CATALOG: list[dict[str, Any]] = [
    {"group": "Core / admin", "env": "FLASK_SECRET_KEY", "label": "Flask secret key",
     "hint": "Session signing — restart service after change", "sensitive": True},
    {"group": "Core / admin", "env": "ADMIN_USER", "label": "Admin username",
     "hint": "Login + Basic Auth", "sensitive": False},
    {"group": "Core / admin", "env": "ADMIN_PASSWORD", "label": "Admin password",
     "hint": "Login + Basic Auth — restart if only in production.env", "sensitive": True},
    {"group": "Core / admin", "env": "ACCOUNTS_ENABLED", "label": "Accounts enabled (0/1)",
     "hint": "User accounts / OAuth gate", "sensitive": False},
    {"group": "Core / admin", "env": "SESSION_COOKIE_SECURE", "label": "Secure session cookies (0/1)",
     "hint": "Force Secure cookie flag", "sensitive": False},
    {"group": "Telegram", "env": "TELEGRAM_BOT_TOKEN", "label": "Telegram bot token",
     "hint": "BotFather token", "sensitive": True},
    {"group": "Telegram", "env": "TELEGRAM_CHAT_ID", "label": "Telegram chat / channel ID",
     "hint": "Usually -100… for channels", "sensitive": False},
    {"group": "Telegram", "env": "TELEGRAM_CHANNEL_URL", "label": "Telegram invite URL",
     "hint": "Public t.me link for CTAs", "sensitive": False},
    {"group": "Telegram", "env": "CHANNEL_AUTO_POST", "label": "Channel auto-post (0/1)",
     "hint": "Morning / post-event auto send", "sensitive": False},
    {"group": "X / Twitter", "env": "TWITTER_API_KEY", "label": "X API key",
     "hint": "OAuth1 consumer key", "sensitive": True},
    {"group": "X / Twitter", "env": "TWITTER_API_SECRET", "label": "X API secret",
     "hint": "OAuth1 consumer secret", "sensitive": True},
    {"group": "X / Twitter", "env": "TWITTER_ACCESS_TOKEN", "label": "X access token",
     "hint": "OAuth1 access token", "sensitive": True},
    {"group": "X / Twitter", "env": "TWITTER_ACCESS_TOKEN_SECRET", "label": "X access token secret",
     "hint": "OAuth1 access secret", "sensitive": True},
    {"group": "SMTP / email", "env": "SMTP_HOST", "label": "SMTP host", "hint": "", "sensitive": False},
    {"group": "SMTP / email", "env": "SMTP_PORT", "label": "SMTP port", "hint": "Usually 587", "sensitive": False},
    {"group": "SMTP / email", "env": "SMTP_USER", "label": "SMTP user", "hint": "", "sensitive": False},
    {"group": "SMTP / email", "env": "SMTP_PASSWORD", "label": "SMTP password", "hint": "", "sensitive": True},
    {"group": "SMTP / email", "env": "SMTP_FROM", "label": "SMTP from address", "hint": "", "sensitive": False},
    {"group": "SMTP / email", "env": "DIGEST_EMAIL_TO", "label": "Digest email to", "hint": "", "sensitive": False},
    {"group": "AI / OCR", "env": "ANTHROPIC_API_KEY", "label": "Anthropic / Claude API key",
     "hint": "Slip OCR", "sensitive": True},
    {"group": "AI / OCR", "env": "OPENAI_API_KEY", "label": "OpenAI API key",
     "hint": "Slip OCR fallback", "sensitive": True},
    {"group": "Sports data APIs", "env": "API_SPORTS_KEY", "label": "API-Sports key",
     "hint": "Football / NFL / baseball / tennis", "sensitive": True},
    {"group": "Sports data APIs", "env": "FOOTBALL_DATA_ORG_TOKEN", "label": "football-data.org token",
     "hint": "", "sensitive": True},
    {"group": "Sports data APIs", "env": "ODDS_API_KEY", "label": "The Odds API key",
     "hint": "", "sensitive": True},
    {"group": "Sports data APIs", "env": "ENTITY_SPORT_TOKEN", "label": "Entity Sport / cricket token",
     "hint": "", "sensitive": True},
    {"group": "Sports data APIs", "env": "RAPIDAPI_KEY", "label": "RapidAPI key",
     "hint": "Dubai RE + misc", "sensitive": True},
    {"group": "Stocks / crypto", "env": "FMP_API_KEY", "label": "Financial Modeling Prep key",
     "hint": "Stock logos / quotes", "sensitive": True},
    {"group": "Stocks / crypto", "env": "COINGECKO_API_KEY", "label": "CoinGecko API key",
     "hint": "Optional higher rate limit", "sensitive": True},
    {"group": "OAuth / Google", "env": "GOOGLE_CLIENT_ID", "label": "Google OAuth client ID",
     "hint": "", "sensitive": False},
    {"group": "OAuth / Google", "env": "GOOGLE_CLIENT_SECRET", "label": "Google OAuth client secret",
     "hint": "", "sensitive": True},
    {"group": "AWS", "env": "AWS_ACCESS_KEY_ID", "label": "AWS access key ID",
     "hint": "Usually empty on EC2 (instance role)", "sensitive": True},
    {"group": "AWS", "env": "AWS_SECRET_ACCESS_KEY", "label": "AWS secret access key",
     "hint": "Usually empty on EC2 (instance role)", "sensitive": True},
    {"group": "AWS", "env": "AWS_SESSION_TOKEN", "label": "AWS session token",
     "hint": "Temporary creds if present", "sensitive": True},
    {"group": "AWS", "env": "AWS_DEFAULT_REGION", "label": "AWS default region",
     "hint": "e.g. us-east-1", "sensitive": False},
    {"group": "AWS", "env": "AWS_REGION", "label": "AWS region", "hint": "", "sensitive": False},
    {"group": "Site / SEO", "env": "SITE_URL", "label": "Site URL", "hint": "", "sensitive": False},
    {"group": "Site / SEO", "env": "INDEXNOW_KEY", "label": "IndexNow key", "hint": "Bing", "sensitive": True},
    {"group": "Site / SEO", "env": "INDEXNOW_ENABLED", "label": "IndexNow enabled (0/1)",
     "hint": "", "sensitive": False},
    {"group": "Site / SEO", "env": "CONTACT_EMAIL", "label": "Contact email", "hint": "", "sensitive": False},
    {"group": "Site / SEO", "env": "INSTAGRAM_URL", "label": "Instagram URL", "hint": "", "sensitive": False},
    {"group": "Site / SEO", "env": "X_URL", "label": "X / Twitter profile URL", "hint": "", "sensitive": False},
]

PRODUCTION_ENV_PATH = REPO / "deploy" / "production.env"

_memory: dict[str, Any] | None = None
_memory_mtime: float | None = None


def _default_settings() -> dict[str, Any]:
    feeds = {}
    for key, meta in FEED_CATALOG.items():
        feeds[key] = dict(meta["defaults"])
    return {
        "version": 1,
        "updated_at": None,
        "telegram": {
            "channel_url": "",
            "chat_id": "",
            "bot_token": "",
            "auto_post": False,
        },
        "apis": {k: "" for k in API_FIELDS},
        "secrets": {row["env"]: "" for row in SECRET_CATALOG},
        "feeds": feeds,
        "notes": "",
    }


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in (overlay or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _read_disk() -> dict[str, Any]:
    try:
        if not SETTINGS_PATH.is_file():
            return {}
        with SETTINGS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.warning("source_settings read failed: %s", exc)
        return {}


def load_settings(*, force: bool = False) -> dict[str, Any]:
    """Merged defaults + disk. Cached by mtime."""
    global _memory, _memory_mtime
    try:
        mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.is_file() else 0.0
    except OSError:
        mtime = 0.0
    if not force and _memory is not None and _memory_mtime == mtime:
        return copy.deepcopy(_memory)
    merged = _deep_merge(_default_settings(), _read_disk())
    # Ensure every catalog sport exists even if file is old.
    for key, meta in FEED_CATALOG.items():
        feeds = merged.setdefault("feeds", {})
        if key not in feeds or not isinstance(feeds[key], dict):
            feeds[key] = dict(meta["defaults"])
        else:
            for dk, dv in meta["defaults"].items():
                feeds[key].setdefault(dk, dv)
    _memory = merged
    _memory_mtime = mtime
    return copy.deepcopy(merged)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate lightly, write atomically, refresh cache."""
    global _memory, _memory_mtime
    current = load_settings(force=True)
    merged = _deep_merge(current, settings or {})
    merged["version"] = 1
    merged["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Clamp feed keys to known option ids.
    feeds = merged.setdefault("feeds", {})
    for sport, meta in FEED_CATALOG.items():
        row = feeds.setdefault(sport, dict(meta["defaults"]))
        if not isinstance(row, dict):
            row = dict(meta["defaults"])
            feeds[sport] = row
        fix_ids = {o[0] for o in meta["fixture_options"]}
        res_ids = {o[0] for o in meta["result_options"]}
        if row.get("fixtures_primary") not in fix_ids:
            row["fixtures_primary"] = meta["defaults"]["fixtures_primary"]
        if row.get("fixtures_fallback") not in fix_ids:
            row["fixtures_fallback"] = meta["defaults"]["fixtures_fallback"]
        if row.get("results_primary") not in res_ids:
            row["results_primary"] = meta["defaults"]["results_primary"]
        if row.get("results_fallback") not in res_ids:
            row["results_fallback"] = meta["defaults"]["results_fallback"]
        row["enabled"] = bool(row.get("enabled", True))

    tg = merged.setdefault("telegram", {})
    tg["channel_url"] = str(tg.get("channel_url") or "").strip()
    tg["chat_id"] = str(tg.get("chat_id") or "").strip()
    tg["bot_token"] = str(tg.get("bot_token") or "").strip()
    tg["auto_post"] = bool(tg.get("auto_post"))

    apis = merged.setdefault("apis", {})
    for key in API_FIELDS:
        apis[key] = str(apis.get(key) or "").strip()

    secrets = merged.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        secrets = {}
        merged["secrets"] = secrets
    for row in SECRET_CATALOG:
        env = row["env"]
        secrets[env] = str(secrets.get(env) or "").strip()

    merged["notes"] = str(merged.get("notes") or "")[:2000]

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(merged, indent=2, ensure_ascii=True, sort_keys=True)
    tmp = None
    try:
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".source-settings-", suffix=".json", dir=str(SETTINGS_PATH.parent),
            )
        except OSError:
            fd, tmp = tempfile.mkstemp(prefix=".source-settings-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SETTINGS_PATH)
        tmp = None
    except OSError:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        log.exception("source_settings disk write failed for %s", SETTINGS_PATH)
        raise

    try:
        _memory_mtime = SETTINGS_PATH.stat().st_mtime
    except OSError:
        _memory_mtime = time.time()
    _memory = merged
    return copy.deepcopy(merged)


def read_production_env() -> dict[str, str]:
    """Parse deploy/production.env KEY=VALUE pairs (no export)."""
    out: dict[str, str] = {}
    try:
        if not PRODUCTION_ENV_PATH.is_file():
            return out
        for raw in PRODUCTION_ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            out[key] = val
    except OSError as exc:
        log.warning("production.env read failed: %s", exc)
    return out


def write_production_env_keys(updates: dict[str, str]) -> bool:
    """Upsert keys in production.env, preserving comments/order when possible."""
    updates = {k: str(v) for k, v in (updates or {}).items() if k}
    if not updates:
        return False
    try:
        PRODUCTION_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        if PRODUCTION_ENV_PATH.is_file():
            lines = PRODUCTION_ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines(True)
        else:
            lines = ["# Managed from admin Sources panel\n"]
    except OSError:
        log.exception("production.env read for write failed")
        return False

    seen = set()
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out_lines.append(f"{key}={updates[key]}\n")
                seen.add(key)
                continue
        out_lines.append(line if line.endswith("\n") else line + "\n")

    missing = [k for k in updates if k not in seen]
    if missing:
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines[-1] += "\n"
        out_lines.append("\n# --- updated from admin Sources panel ---\n")
        for key in missing:
            out_lines.append(f"{key}={updates[key]}\n")

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(
            prefix=".production-env-", suffix=".tmp",
            dir=str(PRODUCTION_ENV_PATH.parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(out_lines))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, PRODUCTION_ENV_PATH)
        tmp = None
        return True
    except OSError:
        log.exception("production.env write failed")
        return False
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def secret_value(env_name: str) -> str:
    """Admin vault → process env → production.env file."""
    env_name = (env_name or "").strip()
    if not env_name:
        return ""
    settings = load_settings()
    stored = ((settings.get("secrets") or {}).get(env_name) or "").strip()
    if stored:
        return stored
    # Legacy mirrors
    if env_name == "TELEGRAM_BOT_TOKEN":
        tg = (settings.get("telegram") or {}).get("bot_token") or ""
        if str(tg).strip():
            return str(tg).strip()
    if env_name == "TELEGRAM_CHAT_ID":
        tg = (settings.get("telegram") or {}).get("chat_id") or ""
        if str(tg).strip():
            return str(tg).strip()
    if env_name == "TELEGRAM_CHANNEL_URL":
        tg = (settings.get("telegram") or {}).get("channel_url") or ""
        if str(tg).strip():
            return str(tg).strip()
    for api_key_name, meta in API_FIELDS.items():
        if meta["env"] == env_name:
            stored_api = ((settings.get("apis") or {}).get(api_key_name) or "").strip()
            if stored_api:
                return stored_api
    live = (os.environ.get(env_name) or "").strip()
    if live:
        return live
    return (read_production_env().get(env_name) or "").strip()


def secrets_vault_view() -> dict[str, Any]:
    """Full plaintext secret inventory for the admin panel."""
    file_env = read_production_env()
    settings = load_settings()
    stored = settings.get("secrets") or {}
    groups: dict[str, list] = {}
    for row in SECRET_CATALOG:
        env = row["env"]
        admin_val = str(stored.get(env) or "").strip()
        proc_val = (os.environ.get(env) or "").strip()
        file_val = (file_env.get(env) or "").strip()
        # Prefer the same resolution order as runtime.
        effective = secret_value(env)
        if admin_val:
            source = "admin"
        elif proc_val:
            source = "process"
        elif file_val:
            source = "production.env"
        else:
            source = "unset"
        groups.setdefault(row["group"], []).append({
            "env": env,
            "label": row["label"],
            "hint": row.get("hint") or "",
            "sensitive": bool(row.get("sensitive")),
            "value": effective,
            "admin_value": admin_val,
            "process_value": proc_val,
            "file_value": file_val,
            "source": source,
            "set": bool(effective),
        })
    group_rows = []
    for name, items in groups.items():
        set_n = sum(1 for i in items if i["set"])
        group_rows.append({
            "name": name,
            "entries": items,
            "count_set": set_n,
            "count_total": len(items),
            "count_unset": len(items) - set_n,
            "complete": set_n == len(items) and len(items) > 0,
        })
    return {
        "groups": group_rows,
        "production_env_path": str(PRODUCTION_ENV_PATH),
        "production_env_exists": PRODUCTION_ENV_PATH.is_file(),
        "count_set": sum(g["count_set"] for g in group_rows),
        "count_total": sum(g["count_total"] for g in group_rows),
        "count_unset": sum(g["count_unset"] for g in group_rows),
    }


def apply_secrets_form(form, *, sync_production_env: bool = True) -> dict[str, Any]:
    """Save full vault from admin form; optionally upsert production.env + os.environ."""
    current = load_settings(force=True)
    secrets = dict(current.get("secrets") or {})
    file_updates: dict[str, str] = {}
    for row in SECRET_CATALOG:
        env = row["env"]
        raw = form.get(f"secret_{env}")
        if raw is None:
            continue
        # Always take submitted value (admin sees plaintext). Empty clears admin override.
        val = str(raw).strip()
        secrets[env] = val
        if val:
            os.environ[env] = val
            file_updates[env] = val
        elif env in os.environ and form.get("secret_clear_" + env) == "1":
            # optional explicit clear — keep process env unless clearing
            pass

    # Keep telegram + apis mirrors in sync for older consumers.
    tg = dict(current.get("telegram") or {})
    if secrets.get("TELEGRAM_BOT_TOKEN") is not None:
        tg["bot_token"] = secrets.get("TELEGRAM_BOT_TOKEN") or ""
    if secrets.get("TELEGRAM_CHAT_ID") is not None:
        tg["chat_id"] = secrets.get("TELEGRAM_CHAT_ID") or ""
    if secrets.get("TELEGRAM_CHANNEL_URL") is not None:
        tg["channel_url"] = secrets.get("TELEGRAM_CHANNEL_URL") or ""
    if "CHANNEL_AUTO_POST" in secrets:
        tg["auto_post"] = str(secrets.get("CHANNEL_AUTO_POST") or "").strip() in (
            "1", "true", "yes", "on",
        )

    apis = dict(current.get("apis") or {})
    for api_name, meta in API_FIELDS.items():
        env = meta["env"]
        if env in secrets:
            apis[api_name] = secrets.get(env) or ""

    saved = save_settings({"secrets": secrets, "telegram": tg, "apis": apis})
    env_written = False
    if sync_production_env and file_updates:
        env_written = write_production_env_keys(file_updates)
    saved["_production_env_written"] = env_written
    return saved


def _mask(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return SECRET_PLACEHOLDER
    return text[:4] + "…" + text[-4:]


def _secret_from_form(raw: str | None, existing: str) -> str:
    """Blank / placeholder / keep → retain existing; else new value."""
    text = (raw or "").strip()
    if not text or text == KEEP_SECRET or text == SECRET_PLACEHOLDER or set(text) <= {"•", "*"}:
        return existing
    return text


def apply_form(form) -> dict[str, Any]:
    """Build a settings patch from a Flask request.form mapping."""
    current = load_settings(force=True)
    patch: dict[str, Any] = {
        "telegram": {},
        "apis": {},
        "secrets": dict(current.get("secrets") or {}),
        "feeds": {},
        "notes": (form.get("notes") or "")[:2000],
    }

    tg = current.get("telegram") or {}
    bot_token = (form.get("tg_bot_token") if form.get("tg_bot_token") is not None else None)
    if bot_token is None:
        bot_token = tg.get("bot_token") or ""
    else:
        bot_token = str(bot_token).strip()
    patch["telegram"] = {
        "channel_url": (form.get("tg_channel_url") or "").strip(),
        "chat_id": (form.get("tg_chat_id") or "").strip(),
        "bot_token": bot_token,
        "auto_post": (form.get("tg_auto_post") or "") in ("1", "on", "true", "yes"),
    }
    patch["secrets"]["TELEGRAM_CHANNEL_URL"] = patch["telegram"]["channel_url"]
    patch["secrets"]["TELEGRAM_CHAT_ID"] = patch["telegram"]["chat_id"]
    patch["secrets"]["TELEGRAM_BOT_TOKEN"] = patch["telegram"]["bot_token"]
    patch["secrets"]["CHANNEL_AUTO_POST"] = "1" if patch["telegram"]["auto_post"] else "0"

    apis_cur = current.get("apis") or {}
    for key, meta in API_FIELDS.items():
        raw = form.get(f"api_{key}")
        if raw is None:
            val = apis_cur.get(key) or ""
        else:
            val = str(raw).strip()
        patch["apis"][key] = val
        patch["secrets"][meta["env"]] = val
        if val:
            os.environ[meta["env"]] = val

    for sport in FEED_CATALOG:
        patch["feeds"][sport] = {
            "enabled": (form.get(f"feed_{sport}_enabled") or "") in ("1", "on", "true", "yes"),
            "fixtures_primary": (form.get(f"feed_{sport}_fixtures_primary") or "").strip(),
            "fixtures_fallback": (form.get(f"feed_{sport}_fixtures_fallback") or "").strip(),
            "results_primary": (form.get(f"feed_{sport}_results_primary") or "").strip(),
            "results_fallback": (form.get(f"feed_{sport}_results_fallback") or "").strip(),
        }

    saved = save_settings(patch)
    # Mirror telegram into process env for this worker.
    for env_name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_CHANNEL_URL", "CHANNEL_AUTO_POST"):
        val = (patch["secrets"].get(env_name) or "").strip()
        if val:
            os.environ[env_name] = val
    return saved


def admin_view_model() -> dict[str, Any]:
    """Admin panel model — includes full plaintext secrets vault."""
    settings = load_settings()
    tg = settings.get("telegram") or {}

    stored_token = secret_value("TELEGRAM_BOT_TOKEN")
    env_chat = secret_value("TELEGRAM_CHAT_ID")
    env_url = secret_value("TELEGRAM_CHANNEL_URL")
    env_auto = secret_value("CHANNEL_AUTO_POST").lower() in (
        "1", "true", "yes", "on",
    ) or bool(tg.get("auto_post"))

    apis_out = {}
    for key, meta in API_FIELDS.items():
        effective = secret_value(meta["env"])
        stored = ((settings.get("apis") or {}).get(key) or "").strip()
        env_val = (os.environ.get(meta["env"]) or "").strip()
        apis_out[key] = {
            **meta,
            "stored": bool(stored),
            "env_set": bool(env_val),
            "effective": bool(effective),
            "display": effective,
            "value": effective,
            "source": "admin" if stored or ((settings.get("secrets") or {}).get(meta["env"])) else (
                "env" if env_val else "unset"
            ),
        }

    feeds_out = []
    status_board = {}
    try:
        import source_health
        status_board = source_health.status_board(force=False)
    except Exception:
        log.exception("source health board failed")
        status_board = {"sports": [], "summary": {}, "checked_at": None}

    status_by_sport: dict[str, dict[str, dict]] = {}
    for sport_row in status_board.get("sports") or []:
        status_by_sport[sport_row["id"]] = {
            opt["id"]: opt for opt in (sport_row.get("options") or [])
        }

    for sport, meta in FEED_CATALOG.items():
        row = (settings.get("feeds") or {}).get(sport) or dict(meta["defaults"])
        opt_status = status_by_sport.get(sport) or {}

        def _opts(pairs):
            out = []
            for oid, olabel in pairs:
                st = opt_status.get(oid) or {}
                out.append({
                    "id": oid,
                    "label": olabel,
                    "status": st.get("status") or "unknown",
                    "detail": st.get("detail") or "",
                    "ms": st.get("ms"),
                    "http": st.get("http"),
                })
            return out

        feeds_out.append({
            "id": sport,
            "label": meta["label"],
            "sport": meta.get("sport") or meta.get("kind") or "",
            "kind": meta.get("kind") or "sports",
            "role_labels": meta.get("role_labels") or {},
            "fixture_options": _opts(meta["fixture_options"]),
            "result_options": _opts(meta["result_options"]),
            # Keep tuple shape for older templates if any.
            "fixture_option_pairs": meta["fixture_options"],
            "result_option_pairs": meta["result_options"],
            "enabled": bool(row.get("enabled", True)),
            "fixtures_primary": row.get("fixtures_primary"),
            "fixtures_fallback": row.get("fixtures_fallback"),
            "results_primary": row.get("results_primary"),
            "results_fallback": row.get("results_fallback"),
            "health": next(
                (s.get("health") for s in (status_board.get("sports") or []) if s.get("id") == sport),
                "unknown",
            ),
        })

    try:
        import feed_store
        feed_health = feed_store.snapshot()
    except Exception:
        feed_health = {}

    investment = {}
    try:
        import investment_sources
        investment = {
            "csv": investment_sources.csv_snapshot(),
            "stocks_enabled": investment_sources.markets_enabled("stocks"),
            "crypto_enabled": investment_sources.markets_enabled("crypto"),
            "stocks_prefs": investment_sources.preferred_providers("stocks"),
            "crypto_prefs": investment_sources.preferred_providers("crypto"),
        }
    except Exception as exc:
        investment = {"error": str(exc)[:120]}

    return {
        "updated_at": settings.get("updated_at"),
        "notes": settings.get("notes") or "",
        "telegram": {
            "channel_url": env_url or (tg.get("channel_url") or ""),
            "chat_id": env_chat or (tg.get("chat_id") or ""),
            "bot_token": stored_token,
            "bot_token_display": stored_token,
            "bot_token_set": bool(stored_token),
            "bot_token_source": "admin" if ((settings.get("secrets") or {}).get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token")) else (
                "env" if (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() else "unset"
            ),
            "auto_post": bool(tg.get("auto_post")) or env_auto,
            "env_channel_url": env_url,
            "env_chat_id": env_chat,
            "env_auto_post": env_auto,
            "effective_channel_url": env_url,
            "effective_chat_id": env_chat,
            "effective_auto_post": env_auto or bool(tg.get("auto_post")),
        },
        "apis": apis_out,
        "feeds": feeds_out,
        "feed_health": feed_health,
        "status_board": status_board,
        "investment": investment,
        "secrets_vault": secrets_vault_view(),
        "secret_placeholder": SECRET_PLACEHOLDER,
    }


def set_feed_enabled(sport: str, enabled: bool) -> dict[str, Any]:
    """Toggle a feed (sport or markets board) on/off without rewriting other fields."""
    sport = (sport or "").strip().lower()
    meta = FEED_CATALOG.get(sport)
    if not meta:
        return {"ok": False, "error": "unknown_sport"}
    settings = load_settings(force=True)
    row = dict((settings.get("feeds") or {}).get(sport) or meta["defaults"])
    row["enabled"] = bool(enabled)
    save_settings({"feeds": {sport: row}})
    return {"ok": True, "sport": sport, "enabled": bool(enabled), "feed": row}


def telegram_credentials() -> dict[str, Any]:
    """Effective Telegram send credentials (admin overrides env)."""
    token = secret_value("TELEGRAM_BOT_TOKEN")
    chat_id = secret_value("TELEGRAM_CHAT_ID")
    channel_url = secret_value("TELEGRAM_CHANNEL_URL")
    auto_raw = secret_value("CHANNEL_AUTO_POST").lower()
    settings = load_settings()
    auto_post = auto_raw in ("1", "true", "yes", "on") or bool(
        (settings.get("telegram") or {}).get("auto_post")
    )
    return {
        "bot_token": token,
        "chat_id": chat_id,
        "channel_url": channel_url,
        "auto_post": auto_post,
    }


def api_key(name: str) -> str:
    """Return admin-stored API key or matching env var."""
    meta = API_FIELDS.get(name)
    if meta:
        return secret_value(meta["env"])
    settings = load_settings()
    return ((settings.get("apis") or {}).get(name) or "").strip()


def feed_preference(sport: str) -> dict[str, Any]:
    """Primary/fallback fixture + result providers for a sport key."""
    settings = load_settings()
    meta = FEED_CATALOG.get(sport) or {}
    defaults = dict(meta.get("defaults") or {})
    row = (settings.get("feeds") or {}).get(sport) or {}
    out = {**defaults, **row}
    out["enabled"] = bool(out.get("enabled", True))
    return out


def provider_order(sport: str, *, kind: str = "fixtures") -> list[str]:
    """Ordered provider ids for fixtures or results (primary → fallback → rest)."""
    pref = feed_preference(sport)
    meta = FEED_CATALOG.get(sport) or {}
    if kind == "results":
        primary = (pref.get("results_primary") or "").strip()
        fallback = (pref.get("results_fallback") or "").strip()
        catalog = [o[0] for o in (meta.get("result_options") or [])]
    else:
        primary = (pref.get("fixtures_primary") or "").strip()
        fallback = (pref.get("fixtures_fallback") or "").strip()
        catalog = [o[0] for o in (meta.get("fixture_options") or [])]
    order: list[str] = []
    for name in (primary, fallback, *catalog):
        if name and name not in order:
            order.append(name)
    return order


def clear_cache() -> None:
    global _memory, _memory_mtime
    _memory = None
    _memory_mtime = None
