"""Admin operators + role permissions (viewer / poster / full)."""
from __future__ import annotations

import secrets
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

ROLES = ("viewer", "poster", "full")

# What each role may do.
ROLE_PERMS: dict[str, set[str]] = {
    "viewer": {"read"},
    "poster": {"read", "post", "ops_safe"},
    "full": {"read", "post", "ops_safe", "ops", "sources", "secrets", "security"},
}


def normalize_role(role: str | None) -> str:
    text = (role or "full").strip().lower()
    return text if text in ROLES else "full"


def has_perm(role: str | None, perm: str) -> bool:
    return perm in ROLE_PERMS.get(normalize_role(role), set())


def hash_password(password: str) -> str:
    return generate_password_hash(password or "", method="pbkdf2:sha256")


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash or password is None:
        return False
    try:
        return bool(check_password_hash(password_hash, password))
    except Exception:
        return False


def operators_from_cfg(cfg: dict) -> list[dict[str, Any]]:
    rows = []
    for raw in cfg.get("operators") or []:
        if not isinstance(raw, dict):
            continue
        username = (raw.get("username") or "").strip()
        if not username:
            continue
        rows.append({
            "username": username,
            "role": normalize_role(raw.get("role")),
            "password_hash": raw.get("password_hash") or "",
            "created_at": raw.get("created_at"),
        })
    return rows


def public_operators(cfg: dict) -> list[dict[str, Any]]:
    return [
        {
            "username": o["username"],
            "role": o["role"],
            "created_at": o.get("created_at"),
        }
        for o in operators_from_cfg(cfg)
    ]


def find_operator(cfg: dict, username: str) -> dict[str, Any] | None:
    want = (username or "").strip().lower()
    for row in operators_from_cfg(cfg):
        if row["username"].lower() == want:
            return row
    return None


def upsert_operator(cfg: dict, *, username: str, password: str, role: str) -> dict:
    import admin_security_cfg
    username = (username or "").strip()
    if not username or len(username) < 2:
        return {"ok": False, "error": "Username too short"}
    if username.lower() == (cfg.get("_primary_user") or "").lower():
        return {"ok": False, "error": "Username reserved for primary admin"}
    role = normalize_role(role)
    if not password or len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters"}
    ops = operators_from_cfg(cfg)
    found = False
    for row in ops:
        if row["username"].lower() == username.lower():
            row["password_hash"] = hash_password(password)
            row["role"] = role
            found = True
            break
    if not found:
        from datetime import datetime, timezone
        ops.append({
            "username": username,
            "role": role,
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        })
    admin_security_cfg.save({"operators": ops})
    return {"ok": True, "username": username, "role": role}


def delete_operator(cfg: dict, username: str) -> dict:
    import admin_security_cfg
    want = (username or "").strip().lower()
    ops = [o for o in operators_from_cfg(cfg) if o["username"].lower() != want]
    admin_security_cfg.save({"operators": ops})
    return {"ok": True}


def new_session_id() -> str:
    return secrets.token_urlsafe(24)
