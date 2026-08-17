"""Live probes for alternate sports data sources (admin Sources panel).

Each provider id maps to a lightweight HTTP check. Results are cached briefly
so opening /admin?section=sources stays responsive. Status values:

  working     — reachable and returned usable data
  degraded    — reachable but empty / thin payload
  down        — timeout, HTTP error, or blocked (e.g. ESPN 403)
  needs_key   — commercial API with no key configured
  history     — offline archive / file-based (not a live probe)
  unknown     — probe failed unexpectedly
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO / "data" / "admin" / "source_health.json"
CACHE_TTL_SECONDS = 90
PROBE_TIMEOUT = 6

HEADERS = {
    "User-Agent": "DegenPredictions/1.0 (+https://degenpredictions.com; source-health)",
    "Accept": "application/json,text/plain,*/*",
}

StatusFn = Callable[[], dict[str, Any]]


def _today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _result(
    status: str,
    *,
    detail: str = "",
    http: int | None = None,
    ms: int | None = None,
    items: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "detail": (detail or "")[:180],
        "http": http,
        "ms": ms,
        "items": items,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _get(url: str, *, params: dict | None = None, headers: dict | None = None,
         timeout: float = PROBE_TIMEOUT):
    import requests
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    t0 = time.time()
    resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
    ms = int((time.time() - t0) * 1000)
    return resp, ms


def _probe_json_ok(url: str, *, params=None, headers=None, min_items=1,
                   count_fn: Callable[[Any], int] | None = None) -> dict[str, Any]:
    try:
        resp, ms = _get(url, params=params, headers=headers)
        if resp.status_code == 403:
            return _result("down", detail="403 forbidden (often blocked from cloud)", http=403, ms=ms)
        if resp.status_code >= 400:
            return _result("down", detail=f"HTTP {resp.status_code}", http=resp.status_code, ms=ms)
        try:
            payload = resp.json()
        except Exception:
            text = (resp.text or "")[:80]
            if resp.status_code == 200 and text:
                return _result("working", detail="non-JSON OK", http=200, ms=ms)
            return _result("degraded", detail="empty/non-JSON body", http=resp.status_code, ms=ms)
        items = 0
        if count_fn:
            try:
                items = int(count_fn(payload))
            except Exception:
                items = 0
        elif isinstance(payload, list):
            items = len(payload)
        elif isinstance(payload, dict):
            for key in ("events", "dates", "games", "matches", "response", "data"):
                val = payload.get(key)
                if isinstance(val, list):
                    items = len(val)
                    break
            if items == 0 and payload.get("totalGames") is not None:
                items = int(payload.get("totalGames") or 0)
        if items >= min_items:
            return _result("working", detail=f"{items} items", http=200, ms=ms, items=items)
        return _result("degraded", detail="reachable but empty", http=200, ms=ms, items=items)
    except Exception as exc:
        return _result("down", detail=f"{type(exc).__name__}: {exc}")


def _needs_key(key_name: str, label: str) -> dict[str, Any]:
    try:
        import source_settings
        if source_settings.api_key(key_name):
            return _result("working", detail=f"{label} key present (live probe skipped)")
    except Exception:
        pass
    return _result("needs_key", detail=f"Add {label} in API keys above")


def _history(path_glob: str, label: str) -> dict[str, Any]:
    root = REPO / "data"
    matches = list(root.glob(path_glob)) if "*" in path_glob else (
        [root / path_glob] if (root / path_glob).exists() else []
    )
    if matches:
        return _result("history", detail=f"{label}: {len(matches)} file(s) on disk", items=len(matches))
    return _result("history", detail=f"{label}: no local files yet")


# ---------------------------------------------------------------------------
# Provider probes (unique across sports; shared ESPN endpoints reuse ids)
# ---------------------------------------------------------------------------

def probe_mlb_statsapi() -> dict[str, Any]:
    day = _today().isoformat()
    return _probe_json_ok(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={"sportId": 1, "date": day, "hydrate": "team"},
        count_fn=lambda d: int((d or {}).get("totalGames") or 0),
    )


def probe_espn_cdn_mlb() -> dict[str, Any]:
    day = _today().strftime("%Y%m%d")
    return _probe_json_ok(
        "https://cdn.espn.com/core/mlb/scoreboard",
        params={"xhr": "1", "date": day},
        count_fn=lambda d: len(
            ((d or {}).get("events") or [])
            or (((d or {}).get("content") or {}).get("sbData") or {}).get("events")
                or []
        ),
    )


def probe_espn_site_mlb() -> dict[str, Any]:
    day = _today().strftime("%Y%m%d")
    return _probe_json_ok(
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
        params={"dates": day},
        count_fn=lambda d: len((d or {}).get("events") or []),
    )


def probe_espn_cdn_nfl() -> dict[str, Any]:
    day = _today().strftime("%Y%m%d")
    return _probe_json_ok(
        "https://cdn.espn.com/core/nfl/scoreboard",
        params={"xhr": "1", "date": day},
        count_fn=lambda d: len(
            ((d or {}).get("events") or [])
            or (((d or {}).get("content") or {}).get("sbData") or {}).get("events")
                or []
        ),
        min_items=0,  # off-season often empty but endpoint can be healthy
    )


def probe_espn_site_nfl() -> dict[str, Any]:
    day = _today().strftime("%Y%m%d")
    out = _probe_json_ok(
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        params={"dates": day},
        count_fn=lambda d: len((d or {}).get("events") or []),
        min_items=0,
    )
    # Treat 200+empty as working in off-season; 403 stays down.
    if out["status"] == "degraded" and out.get("http") == 200:
        out["status"] = "working"
        out["detail"] = "reachable (schedule may be empty off-season)"
    return out


def probe_espn_web_tennis() -> dict[str, Any]:
    return _probe_json_ok(
        "https://site.web.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
        count_fn=lambda d: len((d or {}).get("events") or []),
        min_items=0,
    )


def probe_espn_site_tennis() -> dict[str, Any]:
    return _probe_json_ok(
        "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
        count_fn=lambda d: len((d or {}).get("events") or []),
        min_items=0,
    )


def probe_fixturedownload_soccer() -> dict[str, Any]:
    return _probe_json_ok(
        "https://fixturedownload.com/feed/json/epl-2026",
        count_fn=lambda d: len(d) if isinstance(d, list) else 0,
    )


def probe_fixturedownload_ipl() -> dict[str, Any]:
    year = _today().year
    # Try current then prior season slug.
    for y in (year, year - 1):
        out = _probe_json_ok(
            f"https://fixturedownload.com/feed/json/ipl-{y}",
            count_fn=lambda d: len(d) if isinstance(d, list) else 0,
            min_items=1,
        )
        if out["status"] == "working":
            out["detail"] = f"ipl-{y}: {out.get('detail')}"
            return out
    return out


def probe_football_data_csv() -> dict[str, Any]:
    # Current E0 season CSV — cheap HEAD/GET of first bytes.
    try:
        resp, ms = _get(
            "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
            timeout=PROBE_TIMEOUT,
        )
        if resp.status_code >= 400:
            return _result("down", detail=f"HTTP {resp.status_code}", http=resp.status_code, ms=ms)
        body = resp.text or ""
        if len(body) > 200 and "HomeTeam" in body[:500]:
            return _result("working", detail=f"E0 CSV {len(body)} bytes", http=200, ms=ms)
        return _result("degraded", detail="CSV thin/unexpected", http=200, ms=ms)
    except Exception as exc:
        return _result("down", detail=f"{type(exc).__name__}: {exc}")


def probe_football_data_org() -> dict[str, Any]:
    try:
        import source_settings
        token = source_settings.api_key("football_data_org_token")
    except Exception:
        token = ""
    if not token:
        return _result("needs_key", detail="Add football-data.org token")
    return _probe_json_ok(
        "https://api.football-data.org/v4/competitions/PL/matches",
        headers={"X-Auth-Token": token},
        count_fn=lambda d: len((d or {}).get("matches") or []),
        min_items=0,
    )


def probe_openfootball() -> dict[str, Any]:
    return _probe_json_ok(
        "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json",
        count_fn=lambda d: len((d or {}).get("matches") or []),
        min_items=1,
    )


def probe_nflverse() -> dict[str, Any]:
    try:
        resp, ms = _get(
            "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv",
            timeout=PROBE_TIMEOUT,
        )
        if resp.status_code >= 400:
            return _result("down", detail=f"HTTP {resp.status_code}", http=resp.status_code, ms=ms)
        body = resp.text or ""
        if len(body) > 200 and "home_team" in body[:500]:
            return _result("working", detail=f"games.csv {len(body)} bytes", http=200, ms=ms)
        return _result("degraded", detail="CSV thin/unexpected", http=200, ms=ms)
    except Exception as exc:
        return _result("down", detail=f"{type(exc).__name__}: {exc}")


def probe_spaia() -> dict[str, Any]:
    year = _today().year
    return _probe_json_ok(
        "https://spaia.jp/baseball/npb/api/schedules",
        params={"Year": year},
        count_fn=lambda d: len(d) if isinstance(d, list) else (
            len((d or {}).get("data") or []) if isinstance(d, dict) else 0
        ),
        min_items=1,
    )


def probe_spaia_games_info() -> dict[str, Any]:
    day = _today().strftime("%Y%m%d")
    return _probe_json_ok(
        "https://spaia.jp/baseball/npb/api/games_info_by_date",
        params={"gameDate": day},
        count_fn=lambda d: len(d) if isinstance(d, list) else (
            len((d or {}).get("data") or []) if isinstance(d, dict) else 0
        ),
        min_items=0,
    )


def probe_npbjp() -> dict[str, Any]:
    """npb.jp official schedule page -- HTML, not JSON, so this counts rows
    with a lightweight regex rather than reusing _probe_json_ok's counting."""
    today = _today()
    try:
        resp, ms = _get(
            f"https://npb.jp/games/{today.year}/schedule_{today.month:02d}_detail.html",
        )
        if resp.status_code >= 400:
            return _result("down", detail=f"HTTP {resp.status_code}", http=resp.status_code, ms=ms)
        resp.encoding = "utf-8"
        import re
        items = len(re.findall(r'<tr id="date\d{4}"', resp.text))
        if items > 0:
            return _result("working", detail=f"{items} games this month", http=200, ms=ms, items=items)
        return _result("degraded", detail="reachable but no rows found (off-season?)", http=200, ms=ms, items=0)
    except Exception as exc:
        return _result("down", detail=f"{type(exc).__name__}: {exc}")


def probe_wikipedia() -> dict[str, Any]:
    return _probe_json_ok(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "meta": "siteinfo", "format": "json"},
        count_fn=lambda d: 1 if isinstance(d, dict) and d.get("query") else 0,
    )


def probe_tennismylife() -> dict[str, Any]:
    try:
        resp, ms = _get(
            "https://stats.tennismylife.org/data/ongoing_tourneys.csv",
            timeout=PROBE_TIMEOUT,
        )
        if resp.status_code >= 400:
            return _result("down", detail=f"HTTP {resp.status_code}", http=resp.status_code, ms=ms)
        lines = [ln for ln in (resp.text or "").splitlines() if ln.strip()]
        if len(lines) >= 2:
            return _result("working", detail=f"{len(lines)-1} tourneys", http=200, ms=ms, items=len(lines)-1)
        return _result("degraded", detail="CSV empty", http=200, ms=ms)
    except Exception as exc:
        return _result("down", detail=f"{type(exc).__name__}: {exc}")


def probe_cricsheet() -> dict[str, Any]:
    row = _history("cricket/**/*", "Cricsheet")
    return row


def probe_retrosheet() -> dict[str, Any]:
    return _history("mlb/raw/*", "Retrosheet/season logs")


def probe_api_sports() -> dict[str, Any]:
    return _needs_key("api_sports_key", "API-Sports")


def probe_entity_sport() -> dict[str, Any]:
    return _needs_key("entity_sport_token", "Entity Sport")


def probe_ranked_csv() -> dict[str, Any]:
    """Model board CSV used by /stocks and /crypto."""
    path = REPO / "data" / "investment_scoring" / "ranked_assets.csv"
    if not path.is_file():
        return _result("down", detail="ranked_assets.csv missing — copy from investment_scoring")
    try:
        import pandas as pd
        df = pd.read_csv(path, usecols=lambda c: c in ("asset_class", "ticker", "horizon_months"))
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        n = len(df)
        stocks = int((df["asset_class"].astype(str).str.endswith("stocks")).sum()) if "asset_class" in df.columns else 0
        crypto = int((df["asset_class"].astype(str) == "crypto").sum()) if "asset_class" in df.columns else 0
        detail = f"{n} rows · stocks={stocks} · crypto={crypto} · age={age_h:.0f}h"
        if n < 10:
            return _result("degraded", detail=detail, items=n)
        if age_h > 24 * 45:
            return _result("degraded", detail=detail + " (stale >45d)", items=n)
        return _result("working", detail=detail, items=n)
    except Exception as exc:
        return _result("down", detail=f"CSV read failed: {exc}"[:160])


def probe_yahoo_chart() -> dict[str, Any]:
    return _probe_json_ok(
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        params={"interval": "1d", "range": "5d"},
        headers={"User-Agent": "Mozilla/5.0"},
        count_fn=lambda d: len((((d or {}).get("chart") or {}).get("result") or [])),
        min_items=1,
    )


def probe_tradingview() -> dict[str, Any]:
    try:
        import requests
        t0 = time.time()
        resp = requests.post(
            "https://scanner.tradingview.com/global/scan",
            json={"symbols": {"tickers": ["TVC:GOLD"]}, "columns": ["close"]},
            headers=HEADERS,
            timeout=PROBE_TIMEOUT,
        )
        ms = int((time.time() - t0) * 1000)
        if resp.status_code >= 400:
            return _result("down", detail=f"HTTP {resp.status_code}", http=resp.status_code, ms=ms)
        data = resp.json() if resp.content else {}
        n = len((data or {}).get("data") or [])
        if n < 1:
            return _result("degraded", detail="empty scanner payload", http=resp.status_code, ms=ms, items=0)
        return _result("working", detail=f"{n} symbols", http=resp.status_code, ms=ms, items=n)
    except Exception as exc:
        return _result("down", detail=f"{type(exc).__name__}: {exc}"[:160])


def probe_coingecko() -> dict[str, Any]:
    headers = {}
    try:
        import source_settings
        key = source_settings.api_key("coingecko_api_key") or source_settings.secret_value("COINGECKO_API_KEY")
        if key:
            headers["x-cg-demo-api-key"] = key
    except Exception:
        pass
    return _probe_json_ok(
        "https://api.coingecko.com/api/v3/ping",
        headers=headers or None,
        count_fn=lambda d: 1 if isinstance(d, dict) and d.get("gecko_says") else 0,
        min_items=1,
    )


def probe_binance() -> dict[str, Any]:
    return _probe_json_ok(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "BTCUSDT"},
        count_fn=lambda d: 1 if isinstance(d, dict) and d.get("price") else 0,
        min_items=1,
    )


def probe_fmp() -> dict[str, Any]:
    try:
        import source_settings
        key = source_settings.api_key("fmp_api_key") or source_settings.secret_value("FMP_API_KEY")
    except Exception:
        key = ""
    if not key:
        return _result("needs_key", detail="Add FMP_API_KEY for stock logos/quotes")
    return _probe_json_ok(
        "https://financialmodelingprep.com/api/v3/quote/AAPL",
        params={"apikey": key},
        count_fn=lambda d: len(d) if isinstance(d, list) else (1 if isinstance(d, dict) else 0),
        min_items=1,
    )


PROVIDER_PROBES: dict[str, StatusFn] = {
    "mlb_statsapi": probe_mlb_statsapi,
    "espn_cdn": probe_espn_cdn_mlb,  # default label context is MLB; NFL uses aliases below
    "espn_cdn_mlb": probe_espn_cdn_mlb,
    "espn_cdn_nfl": probe_espn_cdn_nfl,
    "espn_site": probe_espn_site_mlb,
    "espn_site_mlb": probe_espn_site_mlb,
    "espn_site_nfl": probe_espn_site_nfl,
    "espn_web": probe_espn_web_tennis,
    "espn_site_tennis": probe_espn_site_tennis,
    "fixturedownload": probe_fixturedownload_soccer,
    "fixturedownload_ipl": probe_fixturedownload_ipl,
    "football_data_csv": probe_football_data_csv,
    "football_data_org": probe_football_data_org,
    "openfootball": probe_openfootball,
    "nflverse": probe_nflverse,
    "spaia": probe_spaia,
    "spaia_games_info": probe_spaia_games_info,
    "spaia_schedules": probe_spaia,
    "npbjp": probe_npbjp,
    "wikipedia": probe_wikipedia,
    "tennismylife": probe_tennismylife,
    "cricsheet": probe_cricsheet,
    "retrosheet": probe_retrosheet,
    "api_sports": probe_api_sports,
    "entity_sport": probe_entity_sport,
    "ranked_csv": probe_ranked_csv,
    "yahoo_chart": probe_yahoo_chart,
    "tradingview": probe_tradingview,
    "coingecko": probe_coingecko,
    "binance": probe_binance,
    "fmp": probe_fmp,
}

# Sport-aware probe id overrides when the catalog reuses a generic option id.
SPORT_PROBE_ALIAS: dict[str, dict[str, str]] = {
    "nfl": {
        "espn_cdn": "espn_cdn_nfl",
        "espn_site": "espn_site_nfl",
    },
    "tennis": {
        "espn_site": "espn_site_tennis",
    },
    "cricket": {
        "fixturedownload": "fixturedownload_ipl",
    },
}


def _resolve_probe_id(sport: str, provider: str) -> str:
    return (SPORT_PROBE_ALIAS.get(sport) or {}).get(provider, provider)


def _read_cache() -> dict[str, Any] | None:
    try:
        if not CACHE_PATH.is_file():
            return None
        with CACHE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        checked = float(data.get("checked_epoch") or 0)
        if time.time() - checked > CACHE_TTL_SECONDS:
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    """Atomic write; never raise to the admin request (probe results still return)."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("source_health mkdir failed: %s", exc)
        return
    body = json.dumps(payload, indent=2, ensure_ascii=True)
    tmp = None
    try:
        # Prefer same-dir rename; fall back to system temp if data/admin is not writable.
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".source-health-", suffix=".json", dir=str(CACHE_PATH.parent),
            )
        except OSError:
            fd, tmp = tempfile.mkstemp(prefix=".source-health-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, CACHE_PATH)
        tmp = None
    except OSError as exc:
        log.warning("source_health cache write failed: %s", exc)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def probe_providers(provider_ids: list[str], *, force: bool = False) -> dict[str, dict]:
    """Run probes for unique provider ids. Returns id -> status row."""
    cached = None if force else _read_cache()
    out: dict[str, dict] = {}
    if cached:
        out.update(cached.get("providers") or {})

    todo = [pid for pid in provider_ids if pid and (force or pid not in out)]
    if not todo:
        return out

    def _run(pid: str):
        fn = PROVIDER_PROBES.get(pid)
        if not fn:
            return pid, _result("unknown", detail="no probe defined")
        try:
            return pid, fn()
        except Exception as exc:
            return pid, _result("unknown", detail=f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(todo)))) as pool:
        futures = [pool.submit(_run, pid) for pid in todo]
        for fut in as_completed(futures):
            pid, row = fut.result()
            out[pid] = row

    _write_cache({"checked_epoch": time.time(), "providers": out})
    return out


def catalog_provider_ids() -> list[str]:
    import source_settings
    ids: list[str] = []
    seen = set()
    for sport, meta in source_settings.FEED_CATALOG.items():
        for oid, _label in list(meta.get("fixture_options") or []) + list(meta.get("result_options") or []):
            probe_id = _resolve_probe_id(sport, oid)
            if probe_id not in seen:
                seen.add(probe_id)
                ids.append(probe_id)
            if oid not in seen and oid in PROVIDER_PROBES:
                seen.add(oid)
                ids.append(oid)
    return ids


def status_board(*, force: bool = False) -> dict[str, Any]:
    """Per-sport board with every alternate source + live status."""
    import source_settings
    providers = probe_providers(catalog_provider_ids(), force=force)
    settings = source_settings.load_settings()
    sports = []
    summary = {"working": 0, "degraded": 0, "down": 0, "needs_key": 0, "history": 0, "unknown": 0}

    for sport, meta in source_settings.FEED_CATALOG.items():
        pref = (settings.get("feeds") or {}).get(sport) or dict(meta["defaults"])
        options = []
        seen_opt = set()
        for role, key in (
            ("fixtures", "fixture_options"),
            ("results", "result_options"),
        ):
            for oid, label in meta.get(key) or []:
                if oid in seen_opt:
                    continue
                seen_opt.add(oid)
                probe_id = _resolve_probe_id(sport, oid)
                row = providers.get(probe_id) or providers.get(oid) or _result(
                    "unknown", detail="not probed yet",
                )
                st = row.get("status") or "unknown"
                if st in summary:
                    summary[st] += 1
                else:
                    summary["unknown"] += 1
                is_primary = (
                    pref.get("fixtures_primary") == oid
                    or pref.get("results_primary") == oid
                )
                is_fallback = (
                    pref.get("fixtures_fallback") == oid
                    or pref.get("results_fallback") == oid
                )
                options.append({
                    "id": oid,
                    "probe_id": probe_id,
                    "label": label,
                    "roles": role,
                    "status": st,
                    "detail": row.get("detail") or "",
                    "http": row.get("http"),
                    "ms": row.get("ms"),
                    "items": row.get("items"),
                    "checked_at": row.get("checked_at"),
                    "is_primary": is_primary,
                    "is_fallback": is_fallback,
                    "can_switch": st in ("working", "degraded", "history", "needs_key"),
                })

        # Recompute roles properly (fixture vs result lists).
        fix_ids = {o[0] for o in meta.get("fixture_options") or []}
        res_ids = {o[0] for o in meta.get("result_options") or []}
        for opt in options:
            roles = []
            if opt["id"] in fix_ids:
                roles.append("fixtures")
            if opt["id"] in res_ids:
                roles.append("results")
            opt["roles"] = roles
            opt["is_fixtures_primary"] = pref.get("fixtures_primary") == opt["id"]
            opt["is_fixtures_fallback"] = pref.get("fixtures_fallback") == opt["id"]
            opt["is_results_primary"] = pref.get("results_primary") == opt["id"]
            opt["is_results_fallback"] = pref.get("results_fallback") == opt["id"]

        worst = "working"
        rank = {"down": 0, "needs_key": 1, "unknown": 2, "degraded": 3, "history": 4, "working": 5}
        active_ids = {
            pref.get("fixtures_primary"),
            pref.get("fixtures_fallback"),
            pref.get("results_primary"),
            pref.get("results_fallback"),
        }
        for opt in options:
            if opt["id"] not in active_ids:
                continue
            if rank.get(opt["status"], 2) < rank.get(worst, 5):
                worst = opt["status"]

        # Friendly labels for active providers + recommendations when broken.
        by_id = {o["id"]: o for o in options}
        fix_ok_rank = {"working": 0, "degraded": 1, "history": 2}

        def _best_alt(role_prefix: str, current_id: str | None) -> dict | None:
            pool = [
                o for o in options
                if role_prefix in (o.get("roles") or [])
                and o["id"] != current_id
                and o.get("status") in fix_ok_rank
            ]
            pool.sort(key=lambda o: (fix_ok_rank.get(o["status"], 9), o.get("ms") or 9999))
            return pool[0] if pool else None

        def _role_view(role_key: str, provider_id: str | None) -> dict:
            opt = by_id.get(provider_id or "") or {}
            status = opt.get("status") or "unknown"
            broken = status in ("down", "needs_key", "unknown")
            alt = _best_alt("fixtures" if "fixtures" in role_key else "results", provider_id)
            return {
                "role": role_key,
                "provider_id": provider_id,
                "label": opt.get("label") or provider_id or "—",
                "status": status,
                "detail": opt.get("detail") or "",
                "broken": broken,
                "recommend": {
                    "id": alt["id"],
                    "label": alt["label"],
                    "status": alt["status"],
                    "detail": alt.get("detail") or "",
                } if alt else None,
            }

        roles = {
            "fixtures_primary": _role_view("fixtures_primary", pref.get("fixtures_primary")),
            "fixtures_fallback": _role_view("fixtures_fallback", pref.get("fixtures_fallback")),
            "results_primary": _role_view("results_primary", pref.get("results_primary")),
            "results_fallback": _role_view("results_fallback", pref.get("results_fallback")),
        }
        attention = [
            roles[k] for k in ("fixtures_primary", "results_primary")
            if roles[k]["broken"] and roles[k].get("recommend")
        ]

        sports.append({
            "id": sport,
            "label": meta["label"],
            "kind": meta.get("kind") or "sports",
            "role_labels": meta.get("role_labels") or {},
            "enabled": bool(pref.get("enabled", True)),
            "fixtures_primary": pref.get("fixtures_primary"),
            "fixtures_fallback": pref.get("fixtures_fallback"),
            "results_primary": pref.get("results_primary"),
            "results_fallback": pref.get("results_fallback"),
            "options": options,
            "health": worst,
            "roles": roles,
            "attention": attention,
            "can_auto_fix": bool(attention),
        })

    checked_at = None
    for row in providers.values():
        checked_at = row.get("checked_at") or checked_at

    alerts = []
    for sport in sports:
        labels = sport.get("role_labels") or {}
        for item in sport.get("attention") or []:
            role_key = item["role"]
            alerts.append({
                "sport_id": sport["id"],
                "sport_label": sport["label"],
                "role": role_key,
                "role_label": labels.get(role_key) or role_key.replace("_", " "),
                "broken_id": item["provider_id"],
                "broken_label": item["label"],
                "broken_status": item["status"],
                "broken_detail": item.get("detail") or "",
                "recommend_id": (item.get("recommend") or {}).get("id"),
                "recommend_label": (item.get("recommend") or {}).get("label"),
                "recommend_status": (item.get("recommend") or {}).get("status"),
            })

    # Markets (stocks/crypto) first so Sources focuses on boards + quotes.
    sports.sort(key=lambda s: (0 if s.get("kind") == "markets" else 1, s.get("label") or ""))

    return {
        "sports": sports,
        "summary": summary,
        "providers": providers,
        "alerts": alerts,
        "alert_count": len(alerts),
        "checked_at": checked_at,
        "cache_ttl": CACHE_TTL_SECONDS,
    }


def heal_broken_sources(*, force_probe: bool = True) -> dict[str, Any]:
    """One-click: switch every broken primary to the best working alternate."""
    board = status_board(force=force_probe)
    switched = []
    skipped = []
    for alert in board.get("alerts") or []:
        rec = alert.get("recommend_id")
        if not rec:
            skipped.append(alert)
            continue
        result = switch_provider(alert["sport_id"], alert["role"], rec)
        if result.get("ok"):
            switched.append({
                "sport": alert["sport_label"],
                "role": alert["role_label"],
                "from": alert["broken_label"],
                "to": alert["recommend_label"],
            })
        else:
            skipped.append({**alert, "error": result.get("error")})
    return {
        "ok": True,
        "switched": switched,
        "skipped": skipped,
        "count": len(switched),
    }


def switch_provider(sport: str, role: str, provider: str) -> dict[str, Any]:
    """Set fixtures/results primary (and nudge fallback if needed)."""
    import source_settings
    sport = (sport or "").strip().lower()
    role = (role or "").strip().lower()
    provider = (provider or "").strip()
    meta = source_settings.FEED_CATALOG.get(sport)
    if not meta:
        return {"ok": False, "error": "unknown_sport"}
    settings = source_settings.load_settings(force=True)
    row = dict((settings.get("feeds") or {}).get(sport) or meta["defaults"])
    if role == "fixtures_primary":
        ids = {o[0] for o in meta["fixture_options"]}
        if provider not in ids:
            return {"ok": False, "error": "unknown_provider"}
        row["fixtures_primary"] = provider
        if row.get("fixtures_fallback") == provider:
            # Pick another working alternate if possible.
            for oid, _ in meta["fixture_options"]:
                if oid != provider:
                    row["fixtures_fallback"] = oid
                    break
    elif role == "fixtures_fallback":
        ids = {o[0] for o in meta["fixture_options"]}
        if provider not in ids:
            return {"ok": False, "error": "unknown_provider"}
        row["fixtures_fallback"] = provider
    elif role == "results_primary":
        ids = {o[0] for o in meta["result_options"]}
        if provider not in ids:
            return {"ok": False, "error": "unknown_provider"}
        row["results_primary"] = provider
        if row.get("results_fallback") == provider:
            for oid, _ in meta["result_options"]:
                if oid != provider:
                    row["results_fallback"] = oid
                    break
    elif role == "results_fallback":
        ids = {o[0] for o in meta["result_options"]}
        if provider not in ids:
            return {"ok": False, "error": "unknown_provider"}
        row["results_fallback"] = provider
    else:
        return {"ok": False, "error": "unknown_role"}

    source_settings.save_settings({"feeds": {sport: row}})
    return {"ok": True, "sport": sport, "role": role, "provider": provider, "feed": row}
