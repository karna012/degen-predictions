"""Admin security extras: IP allowlist, session epoch, idle timeout, alert prefs."""
from __future__ import annotations

import ipaddress
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PATH = Path(os.environ.get("ADMIN_SECURITY_PATH") or (REPO / "data" / "admin" / "security.json"))


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _defaults() -> dict[str, Any]:
    return {
        "version": 1,
        "ip_allowlist": [],          # empty = allow all
        "idle_timeout_minutes": 0,   # 0 = disabled
        "session_epoch": 1,
        "alert_telegram_on_down": True,
        "alert_telegram_on_stale_rankings": True,
        "alert_cooldown_minutes": 60,
        "operators": [],
        "sessions": [],
        "updated_at": None,
    }


def load(*, force: bool = False) -> dict[str, Any]:
    base = _defaults()
    if not PATH.is_file():
        return base
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(raw, dict):
        return base
    out = {**base, **raw}
    out["ip_allowlist"] = [
        str(x).strip() for x in (out.get("ip_allowlist") or []) if str(x).strip()
    ]
    try:
        out["idle_timeout_minutes"] = max(0, int(out.get("idle_timeout_minutes") or 0))
    except (TypeError, ValueError):
        out["idle_timeout_minutes"] = 0
    try:
        out["session_epoch"] = int(out.get("session_epoch") or 1)
    except (TypeError, ValueError):
        out["session_epoch"] = 1
    out["alert_telegram_on_down"] = bool(out.get("alert_telegram_on_down", True))
    out["alert_telegram_on_stale_rankings"] = bool(out.get("alert_telegram_on_stale_rankings", True))
    try:
        out["alert_cooldown_minutes"] = max(5, int(out.get("alert_cooldown_minutes") or 60))
    except (TypeError, ValueError):
        out["alert_cooldown_minutes"] = 60
    out["operators"] = list(out.get("operators") or [])
    out["sessions"] = list(out.get("sessions") or [])
    return out


def save(patch: dict[str, Any]) -> dict[str, Any]:
    cur = load(force=True)
    cur.update(patch or {})
    cur["updated_at"] = _utcnow()
    # Normalize allowlist from textarea / list
    raw_list = cur.get("ip_allowlist") or []
    if isinstance(raw_list, str):
        raw_list = raw_list.replace(",", "\n").splitlines()
    cleaned = []
    for item in raw_list:
        text = str(item).strip()
        if not text or text.startswith("#"):
            continue
        cleaned.append(text)
    cur["ip_allowlist"] = cleaned
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(PATH)
    try:
        PATH.chmod(0o600)
    except OSError:
        pass
    return cur


def bump_session_epoch() -> int:
    cur = load(force=True)
    nxt = int(cur.get("session_epoch") or 1) + 1
    save({"session_epoch": nxt, "sessions": []})
    return nxt


def register_session(*, session_id: str, actor: str, role: str, ip: str, ua: str) -> None:
    cur = load(force=True)
    rows = [s for s in (cur.get("sessions") or []) if isinstance(s, dict) and s.get("id") != session_id]
    rows.insert(0, {
        "id": session_id,
        "actor": (actor or "admin")[:80],
        "role": (role or "full")[:20],
        "ip": (ip or "")[:80],
        "ua": (ua or "")[:160],
        "created_at": _utcnow(),
        "last_seen": _utcnow(),
    })
    save({"sessions": rows[:25]})


def touch_session(session_id: str, *, ip: str = "") -> None:
    if not session_id:
        return
    cur = load(force=True)
    changed = False
    rows = []
    for s in cur.get("sessions") or []:
        if not isinstance(s, dict):
            continue
        if s.get("id") == session_id:
            s = dict(s)
            s["last_seen"] = _utcnow()
            if ip:
                s["ip"] = ip[:80]
            changed = True
        rows.append(s)
    if changed:
        save({"sessions": rows})


def revoke_session(session_id: str) -> bool:
    cur = load(force=True)
    before = len(cur.get("sessions") or [])
    rows = [s for s in (cur.get("sessions") or []) if isinstance(s, dict) and s.get("id") != session_id]
    save({"sessions": rows})
    return len(rows) < before


def list_sessions() -> list[dict[str, Any]]:
    return [s for s in (load().get("sessions") or []) if isinstance(s, dict)]


def ip_allowed(client_ip: str) -> bool:
    """Empty allowlist → allow everyone. Supports single IPs and CIDR."""
    rules = load().get("ip_allowlist") or []
    if not rules:
        return True
    text = (client_ip or "").strip()
    if not text or text in ("127.0.0.1", "::1"):
        # Always allow local/loopback (health checks / SSH tunnels).
        return True
    try:
        addr = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    for rule in rules:
        try:
            if "/" in rule:
                if addr in ipaddress.ip_network(rule, strict=False):
                    return True
            elif addr == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False


def apply_form(form) -> dict[str, Any]:
    raw_ips = (form.get("ip_allowlist") or "").strip()
    idle = form.get("idle_timeout_minutes") or "0"
    try:
        idle_n = max(0, min(24 * 60, int(idle)))
    except (TypeError, ValueError):
        idle_n = 0
    try:
        cool = max(5, min(24 * 60, int(form.get("alert_cooldown_minutes") or 60)))
    except (TypeError, ValueError):
        cool = 60
    return save({
        "ip_allowlist": raw_ips,
        "idle_timeout_minutes": idle_n,
        "alert_telegram_on_down": (form.get("alert_telegram_on_down") or "") == "1",
        "alert_telegram_on_stale_rankings": (form.get("alert_telegram_on_stale_rankings") or "") == "1",
        "alert_cooldown_minutes": cool,
    })
