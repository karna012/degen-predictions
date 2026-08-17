"""Admin ops: system health, one-click jobs, down-source Telegram alerts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
ALERT_STATE = REPO / "data" / "admin" / "source_alert_state.json"
JOB_LOG = REPO / "data" / "admin" / "ops_jobs.jsonl"
UNITS = (
    "degen-predictions.service",
    "degen-refresh-data.timer",
    "degen-daily-distribute.timer",
    "degen-post-event.timer",
    "degen-live-tracker.timer",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _systemctl(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["systemctl", *args],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        ).strip()
    except Exception:
        return ""


def unit_status(name: str) -> dict[str, Any]:
    active = _systemctl("is-active", name) or "unknown"
    enabled = _systemctl("is-enabled", name) or "unknown"
    last = ""
    if name.endswith(".timer"):
        last = _systemctl("show", name, "-p", "LastTriggerUSec", "--value")
    elif name.endswith(".service"):
        last = _systemctl("show", name, "-p", "ActiveEnterTimestamp", "--value")
    return {
        "name": name,
        "active": active,
        "enabled": enabled,
        "last": last or "—",
        "ok": active in ("active", "waiting"),
    }


def disk_usage() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(REPO))
        total = max(1, usage.total)
        return {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "used_pct": round(100.0 * usage.used / total, 1),
        }
    except OSError:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "used_pct": 0}


def health_board() -> dict[str, Any]:
    import source_health
    board = source_health.status_board(force=False)
    units = [unit_status(u) for u in UNITS]
    digest_info = _digest_health()
    return {
        "checked_at": _utcnow(),
        "disk": disk_usage(),
        "units": units,
        "sources": board.get("summary") or {},
        "alert_count": board.get("alert_count") or 0,
        "alerts": board.get("alerts") or [],
        "service_ok": all(u["ok"] for u in units if u["name"] == "degen-predictions.service"),
        "digest": digest_info,
        "recent_jobs": recent_jobs(8),
        "investment": _investment_health(),
    }


def _investment_health() -> dict[str, Any]:
    try:
        import investment_sources
        snap = investment_sources.csv_snapshot()
        return {
            "csv": snap,
            "stocks_enabled": investment_sources.markets_enabled("stocks"),
            "crypto_enabled": investment_sources.markets_enabled("crypto"),
            "stocks_prefs": investment_sources.preferred_providers("stocks"),
            "crypto_prefs": investment_sources.preferred_providers("crypto"),
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _digest_health() -> dict[str, Any]:
    out = {
        "today": None,
        "state_keys": [],
        "latest_digest": None,
        "twitter_queue": 0,
        "last_telegram_send": None,
    }
    try:
        import daily_digest
        out["today"] = daily_digest.today_key()
        state = daily_digest.load_state() or {}
        out["state_keys"] = sorted(str(k) for k in state.keys())[:12]
        latest = daily_digest.load_latest_digest()
        if latest:
            out["latest_digest"] = {
                "date": latest.get("date"),
                "picks": len(latest.get("picks") or []),
                "combos": daily_digest._combo_count(latest.get("combos")),
            }
        try:
            q = daily_digest.twitter_queue() or []
            out["twitter_queue"] = len(q)
        except Exception:
            out["twitter_queue"] = 0
        last = daily_digest.load_last_telegram_send()
        if last:
            out["last_telegram_send"] = {
                "day": last.get("day"),
                "count": len(last.get("slips") or []),
                "saved_at": last.get("saved_at") or last.get("at"),
            }
    except Exception as exc:
        out["error"] = str(exc)[:120]
    return out


def recent_jobs(limit: int = 12) -> list[dict[str, Any]]:
    if not JOB_LOG.is_file():
        return []
    try:
        lines = JOB_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-80:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return list(reversed(rows[-limit:]))


def _log_job(result: dict[str, Any]) -> None:
    try:
        JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
        with JOB_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": _utcnow(),
                "job": result.get("job"),
                "ok": result.get("ok"),
                "detail": (result.get("detail") or result.get("error") or "")[:240],
                "ms": result.get("ms"),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_job(job_id: str) -> dict[str, Any]:
    """Safe one-click admin jobs (no arbitrary shell)."""
    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "probe_sources": _job_probe,
        "heal_sources": _job_heal,
        "clear_mlb_cache": _job_clear_mlb,
        "clear_nfl_cache": _job_clear_nfl,
        "clear_soccer_cache": _job_clear_soccer,
        "alert_down_now": _job_alert_now,
        "build_digest": _job_build_digest,
        "settle_due_results": _job_settle_due,
        "refresh_accuracy": _job_refresh_accuracy,
        "clear_rankings_cache": _job_clear_rankings,
        "refresh_asset_logos": _job_refresh_logos,
        "alert_stale_rankings": _job_alert_stale_rankings,
    }
    fn = jobs.get(job_id)
    if not fn:
        return {"ok": False, "error": "unknown_job"}
    started = time.time()
    try:
        result = fn()
        result["ok"] = bool(result.get("ok", True))
        result["job"] = job_id
        result["ms"] = int((time.time() - started) * 1000)
        _log_job(result)
        return result
    except Exception as exc:
        result = {"ok": False, "job": job_id, "error": str(exc)[:200]}
        _log_job(result)
        return result


def _job_probe() -> dict[str, Any]:
    import source_health
    board = source_health.status_board(force=True)
    return {
        "ok": True,
        "detail": (
            f"working={board['summary'].get('working', 0)} "
            f"down={board['summary'].get('down', 0)} "
            f"broken={board.get('alert_count', 0)}"
        ),
    }


def _job_heal() -> dict[str, Any]:
    import source_health
    result = source_health.heal_broken_sources(force_probe=True)
    n = int(result.get("count") or 0)
    return {"ok": True, "detail": f"fixed {n}", "count": n}


def _job_clear_mlb() -> dict[str, Any]:
    import mlb_fixtures
    mlb_fixtures._cache.clear()
    if hasattr(mlb_fixtures, "_pitcher_cache"):
        mlb_fixtures._pitcher_cache.clear()
    return {"ok": True, "detail": "MLB fixture cache cleared"}


def _job_clear_nfl() -> dict[str, Any]:
    import nfl_fixtures
    nfl_fixtures._cache.clear()
    return {"ok": True, "detail": "NFL fixture cache cleared"}


def _job_clear_soccer() -> dict[str, Any]:
    import soccer_fixtures
    soccer_fixtures.clear_cache()
    return {"ok": True, "detail": "Soccer fixture cache cleared"}


def _job_alert_now() -> dict[str, Any]:
    sent = maybe_alert_down_sources(force=True)
    return {"ok": True, "detail": sent.get("detail") or "done", **sent}


def _job_build_digest() -> dict[str, Any]:
    """Build + write today's digest locally (does not auto-post to channels)."""
    import daily_digest
    result = daily_digest.run_daily_job(skip_posts=True) or {}
    return {
        "ok": True,
        "detail": (
            f"digest {result.get('date')} · picks={result.get('pick_count', 0)} "
            f"combos={result.get('featured_combo_count', 0)} (no channel post)"
        ),
    }


def _job_settle_due() -> dict[str, Any]:
    """Grade/post due Telegram results without forcing X."""
    import daily_digest
    stats = daily_digest.post_due_telegram_results(do_twitter=False) or {}
    return {
        "ok": True,
        "detail": (
            f"checked={stats.get('checked', 0)} "
            f"posted={stats.get('posted', 0)} "
            f"skipped={stats.get('skipped', 0)}"
        ),
    }


def _job_refresh_accuracy() -> dict[str, Any]:
    """Invalidate results HTML cache so next /results?refresh rebuilds."""
    cache = REPO / "webapp" / "results_cache.html"
    removed = False
    if cache.is_file():
        cache.unlink(missing_ok=True)
        removed = True
    return {"ok": True, "detail": "results cache cleared" if removed else "no cache file"}


def _job_clear_rankings() -> dict[str, Any]:
    import investment_sources
    detail = investment_sources.clear_rankings_dataframe_cache()
    return {"ok": True, "detail": detail}


def _job_refresh_logos() -> dict[str, Any]:
    """Re-attach CoinGecko/FMP logos onto a sample of ranked assets (cache warm)."""
    import asset_logos
    import pandas as pd
    path = REPO / "data" / "investment_scoring" / "ranked_assets.csv"
    if not path.is_file():
        return {"ok": False, "error": "ranked_assets.csv missing"}
    df = pd.read_csv(path)
    if df.empty:
        return {"ok": False, "error": "CSV empty"}
    sample = df.head(80).copy()
    out = asset_logos.attach_logo_urls(sample)
    filled = 0
    if "logo_url" in out.columns:
        filled = int(out["logo_url"].astype(str).str.startswith("http").sum() + out["logo_url"].astype(str).str.startswith("/static/").sum())
    return {"ok": True, "detail": f"warmed logos on {len(out)} rows · {filled} with url"}


def _job_alert_stale_rankings() -> dict[str, Any]:
    sent = maybe_alert_stale_rankings(force=True)
    return {"ok": True, "detail": sent.get("detail") or "done", **sent}


def _load_alert_state() -> dict:
    if not ALERT_STATE.is_file():
        return {}
    try:
        return json.loads(ALERT_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_alert_state(data: dict) -> None:
    try:
        ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def maybe_alert_down_sources(*, force: bool = False) -> dict[str, Any]:
    """Telegram ping when primaries are broken (cooldown)."""
    import admin_security_cfg
    import source_health
    cfg = admin_security_cfg.load()
    if not cfg.get("alert_telegram_on_down") and not force:
        return {"sent": False, "detail": "alerts disabled"}

    board = source_health.status_board(force=force)
    alerts = board.get("alerts") or []
    if not alerts:
        return {"sent": False, "detail": "nothing broken"}

    key = "|".join(sorted(f"{a.get('sport_id')}:{a.get('role')}" for a in alerts))
    state = _load_alert_state()
    now = time.time()
    cool = int(cfg.get("alert_cooldown_minutes") or 60) * 60
    if not force and state.get("key") == key and (now - float(state.get("ts") or 0)) < cool:
        return {"sent": False, "detail": "cooldown"}

    try:
        import source_settings
        creds = source_settings.telegram_credentials()
        token = (creds.get("bot_token") or "").strip()
        chat_id = (creds.get("chat_id") or "").strip()
    except Exception:
        token = chat_id = ""
    if not token or not chat_id:
        return {"sent": False, "detail": "telegram not configured"}

    lines = ["⚠ Degen admin: broken data sources"]
    for a in alerts[:8]:
        lines.append(
            f"• {a.get('sport_label')} {a.get('role_label')}: "
            f"{a.get('broken_label')} → try {a.get('recommend_label') or 'n/a'}"
        )
    lines.append("Fix: https://degenpredictions.com/admin?section=sources")
    text = "\n".join(lines)

    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=12,
        )
        ok = resp.status_code == 200 and bool((resp.json() or {}).get("ok"))
    except Exception as exc:
        return {"sent": False, "detail": f"send failed: {exc}"[:160]}

    if ok:
        merged = dict(state)
        merged.update({"key": key, "ts": now, "at": _utcnow()})
        _save_alert_state(merged)
    return {"sent": ok, "detail": f"{'sent' if ok else 'telegram error'} ({len(alerts)} alerts)"}


def maybe_alert_stale_rankings(*, force: bool = False) -> dict[str, Any]:
    """Telegram ping when ranked_assets.csv is missing or stale (>45d)."""
    import admin_security_cfg
    import investment_sources
    cfg = admin_security_cfg.load()
    if not cfg.get("alert_telegram_on_stale_rankings") and not force:
        return {"sent": False, "detail": "stale rankings alerts disabled"}

    snap = investment_sources.csv_snapshot()
    if snap.get("exists") and not snap.get("stale") and not snap.get("error"):
        return {"sent": False, "detail": "CSV fresh"}

    reason = "missing"
    if snap.get("error"):
        reason = f"error:{snap.get('error')}"
    elif snap.get("stale"):
        reason = f"stale:{snap.get('age_hours')}h"
    key = f"rankings:{reason}"
    state = _load_alert_state()
    now = time.time()
    cool = int(cfg.get("alert_cooldown_minutes") or 60) * 60
    stale_state = state.get("rankings") or {}
    if not force and stale_state.get("key") == key and (now - float(stale_state.get("ts") or 0)) < cool:
        return {"sent": False, "detail": "cooldown"}

    try:
        import source_settings
        creds = source_settings.telegram_credentials()
        token = (creds.get("bot_token") or "").strip()
        chat_id = (creds.get("chat_id") or "").strip()
    except Exception:
        token = chat_id = ""
    if not token or not chat_id:
        return {"sent": False, "detail": "telegram not configured"}

    if not snap.get("exists"):
        text = (
            "⚠ Degen admin: ranked_assets.csv missing\n"
            "Stocks/crypto boards need a fresh CSV from the investment_scoring pipeline.\n"
            "Upload: https://degenpredictions.com/admin?section=tools"
        )
    else:
        text = (
            f"⚠ Degen admin: ranked_assets.csv stale ({snap.get('age_hours')}h old)\n"
            f"Rows={snap.get('rows')} · stocks={snap.get('stocks_rows')} · crypto={snap.get('crypto_rows')}\n"
            "Upload a fresh CSV in Admin → Tools."
        )

    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=12,
        )
        ok = resp.status_code == 200 and bool((resp.json() or {}).get("ok"))
    except Exception as exc:
        return {"sent": False, "detail": f"send failed: {exc}"[:160]}

    if ok:
        merged = dict(state)
        merged["rankings"] = {"key": key, "ts": now, "at": _utcnow()}
        _save_alert_state(merged)
    return {"sent": ok, "detail": f"{'sent' if ok else 'telegram error'} ({reason})"}
