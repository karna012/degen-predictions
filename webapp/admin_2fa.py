"""Admin TOTP 2FA (authenticator apps) + one-time backup codes.

Browser login: password → 6-digit code (when enabled).
Basic Auth (scripts): password|123456 or X-Admin-OTP header when 2FA is on.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyotp

REPO = Path(__file__).resolve().parents[1]
STATE_PATH = Path(os.environ.get("ADMIN_2FA_PATH") or (REPO / "data" / "admin" / "2fa.json"))
ISSUER = "Degen Predictions Admin"
BACKUP_COUNT = 8


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "enabled": False,
        "secret": "",
        "pending_secret": "",
        "backup_hashes": [],
        "enrolled_at": None,
        "last_verified_at": None,
    }


def load_state(*, force: bool = False) -> dict[str, Any]:
    base = _default_state()
    if not STATE_PATH.is_file():
        return base
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(raw, dict):
        return base
    out = {**base, **raw}
    out["enabled"] = bool(out.get("enabled")) and bool(out.get("secret"))
    out["backup_hashes"] = list(out.get("backup_hashes") or [])
    return out


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    cur = load_state(force=True)
    cur.update(patch or {})
    cur["enabled"] = bool(cur.get("enabled")) and bool(cur.get("secret"))
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)
    try:
        STATE_PATH.chmod(0o600)
    except OSError:
        pass
    return cur


def is_enabled() -> bool:
    st = load_state()
    return bool(st.get("enabled") and st.get("secret"))


def _hash_backup(code: str) -> str:
    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _make_backup_codes(n: int = BACKUP_COUNT) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(n):
        codes.append("".join(secrets.choice(alphabet) for _ in range(8)))
    return codes


def provisioning_uri(secret: str, account_name: str = "admin") -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=account_name or "admin", issuer_name=ISSUER)


def qr_data_uri(secret: str, account_name: str = "admin") -> str:
    """PNG data-URI QR for authenticator enrollment."""
    import qrcode

    uri = provisioning_uri(secret, account_name=account_name)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def start_setup(account_name: str = "admin") -> dict[str, Any]:
    """Begin enrollment: store pending secret, return QR + manual key."""
    secret = pyotp.random_base32()
    save_state({"pending_secret": secret})
    return {
        "secret": secret,
        "uri": provisioning_uri(secret, account_name=account_name),
        "qr_data_uri": qr_data_uri(secret, account_name=account_name),
        "account": account_name,
        "issuer": ISSUER,
    }


def confirm_setup(code: str) -> dict[str, Any]:
    """Verify first OTP against pending secret and enable 2FA."""
    st = load_state(force=True)
    pending = (st.get("pending_secret") or "").strip()
    if not pending:
        return {"ok": False, "error": "No setup in progress — start setup again."}
    if not verify_totp(pending, code):
        return {"ok": False, "error": "Invalid code — check your authenticator and try again."}
    backups = _make_backup_codes()
    save_state({
        "secret": pending,
        "pending_secret": "",
        "enabled": True,
        "backup_hashes": [_hash_backup(c) for c in backups],
        "enrolled_at": _utcnow(),
        "last_verified_at": _utcnow(),
    })
    return {"ok": True, "backup_codes": backups}


def cancel_setup() -> None:
    save_state({"pending_secret": ""})


def disable(code: str) -> dict[str, Any]:
    """Disable 2FA after a valid TOTP or backup code."""
    if not is_enabled():
        return {"ok": True, "already": True}
    ok, used_backup = verify_any(code)
    if not ok:
        return {"ok": False, "error": "Invalid authenticator or backup code."}
    if used_backup:
        _consume_backup(code)
    save_state({
        "enabled": False,
        "secret": "",
        "pending_secret": "",
        "backup_hashes": [],
        "enrolled_at": None,
    })
    return {"ok": True}


def regenerate_backups(code: str) -> dict[str, Any]:
    if not is_enabled():
        return {"ok": False, "error": "2FA is not enabled."}
    ok, _ = verify_any(code)
    if not ok:
        return {"ok": False, "error": "Invalid authenticator or backup code."}
    backups = _make_backup_codes()
    save_state({
        "backup_hashes": [_hash_backup(c) for c in backups],
        "last_verified_at": _utcnow(),
    })
    return {"ok": True, "backup_codes": backups}


def verify_totp(secret: str, code: str, *, window: int = 1) -> bool:
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) != 6 or not secret:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(digits, valid_window=window))
    except Exception:
        return False


def verify_backup(code: str, hashes: list[str] | None = None) -> bool:
    h = _hash_backup(code)
    pool = hashes if hashes is not None else (load_state().get("backup_hashes") or [])
    return any(secrets.compare_digest(h, existing) for existing in pool if existing)


def verify_any(code: str) -> tuple[bool, bool]:
    """Return (ok, used_backup)."""
    st = load_state(force=True)
    secret = (st.get("secret") or "").strip()
    if not secret or not st.get("enabled"):
        return False, False
    if verify_totp(secret, code):
        save_state({"last_verified_at": _utcnow()})
        return True, False
    if verify_backup(code, st.get("backup_hashes") or []):
        return True, True
    return False, False


def _consume_backup(code: str) -> None:
    st = load_state(force=True)
    target = _hash_backup(code)
    remaining = [
        h for h in (st.get("backup_hashes") or [])
        if not secrets.compare_digest(h, target)
    ]
    save_state({"backup_hashes": remaining, "last_verified_at": _utcnow()})


def verify_and_consume(code: str) -> bool:
    ok, used_backup = verify_any(code)
    if ok and used_backup:
        _consume_backup(code)
    return ok


def status_view() -> dict[str, Any]:
    st = load_state()
    return {
        "enabled": bool(st.get("enabled")),
        "enrolled_at": st.get("enrolled_at"),
        "last_verified_at": st.get("last_verified_at"),
        "backup_remaining": len(st.get("backup_hashes") or []),
        "setup_pending": bool(st.get("pending_secret")),
        "issuer": ISSUER,
    }


def split_basic_password(raw: str) -> tuple[str, str]:
    """Support password|123456 for Basic Auth when 2FA is enabled."""
    text = raw or ""
    if "|" not in text:
        return text, ""
    password, otp = text.rsplit("|", 1)
    return password, otp.strip()
