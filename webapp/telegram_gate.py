"""Telegram side of the sports-prediction unlock gate.

Reuses the bot credentials daily_digest.py already posts picks with (the
bot is already an admin of the channel to post there, which is also what
getChatMember needs to check membership -- no new bot/permissions required).

Flow: visitor taps a t.me/<bot>?start=<code> deep link -> Telegram opens a
chat with the bot and sends "/start <code>" -> our webhook receives it,
looks up the code, checks channel membership, and replies to the visitor.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

import requests

import daily_digest

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
_SECRET_PATH = Path(
    os.environ.get("TELEGRAM_GATE_SECRET_PATH") or (REPO / "data" / "telegram_gate.json"),
)

_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}

_bot_username_cache: dict = {"value": "", "checked_at": 0.0}
BOT_USERNAME_TTL_SECONDS = 6 * 60 * 60


def _api(method: str) -> str | None:
    token, _chat_id = daily_digest.telegram_bot_credentials()
    if not token:
        return None
    return f"https://api.telegram.org/bot{token}/{method}"


def webhook_secret() -> str:
    """Random per-deploy token Telegram echoes back on every webhook POST
    (X-Telegram-Bot-Api-Secret-Token header) so we can reject spoofed
    requests. Auto-generated once and persisted -- same pattern as
    admin_security_cfg.py's local JSON config."""
    env_val = (os.environ.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if env_val:
        return env_val
    try:
        raw = json.loads(_SECRET_PATH.read_text(encoding="utf-8"))
        val = str(raw.get("webhook_secret") or "").strip()
        if val:
            return val
    except (OSError, json.JSONDecodeError):
        pass
    val = secrets.token_urlsafe(32)
    try:
        _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SECRET_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"webhook_secret": val}), encoding="utf-8")
        tmp.replace(_SECRET_PATH)
        try:
            _SECRET_PATH.chmod(0o600)
        except OSError:
            pass
    except OSError:
        log.exception("could not persist telegram webhook secret")
    return val


def verify_webhook_secret(header_value: str) -> bool:
    expected = webhook_secret()
    if not expected:
        return False
    return secrets.compare_digest((header_value or ""), expected)


def bot_username(*, force: bool = False) -> str:
    now = time.time()
    if not force and _bot_username_cache["value"] and now - _bot_username_cache["checked_at"] < BOT_USERNAME_TTL_SECONDS:
        return _bot_username_cache["value"]
    url = _api("getMe")
    if not url:
        return _bot_username_cache["value"]
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        username = ((data.get("result") or {}).get("username") or "").strip()
        if username:
            _bot_username_cache["value"] = username
            _bot_username_cache["checked_at"] = now
            return username
    except (requests.RequestException, ValueError):
        log.warning("telegram getMe failed", exc_info=True)
    return _bot_username_cache["value"]


def deep_link(code: str) -> str:
    username = bot_username()
    if not username or not code:
        return ""
    return f"https://t.me/{username}?start={code}"


def is_chat_member(user_id: int | str) -> bool | None:
    """True/False, or None if the check itself failed (network/API error) --
    callers should treat None as "don't unlock, but don't blame the user"."""
    url = _api("getChatMember")
    _token, chat_id = daily_digest.telegram_bot_credentials()
    if not url or not chat_id:
        return None
    try:
        resp = requests.get(
            url, params={"chat_id": chat_id, "user_id": user_id}, timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return False
        status = ((data.get("result") or {}).get("status") or "").strip().lower()
        return status in _MEMBER_STATUSES
    except (requests.RequestException, ValueError):
        log.warning("telegram getChatMember failed", exc_info=True)
        return None


def send_dm(user_id: int | str, text: str) -> bool:
    url = _api("sendMessage")
    if not url:
        return False
    try:
        resp = requests.post(
            url, json={"chat_id": user_id, "text": text[:4096]}, timeout=10,
        )
        return bool(resp.ok)
    except requests.RequestException:
        log.warning("telegram sendMessage (DM) failed", exc_info=True)
        return False


def register_webhook(base_url: str) -> tuple[bool, str]:
    """One-time (per deploy) setup: point Telegram at our webhook route.
    Call from an admin action after the site is live at a real HTTPS URL --
    Telegram will not deliver updates to localhost."""
    url = _api("setWebhook")
    if not url:
        return False, "no TELEGRAM_BOT_TOKEN configured"
    hook_url = base_url.rstrip("/") + "/telegram/webhook"
    try:
        resp = requests.post(
            url,
            json={
                "url": hook_url,
                "secret_token": webhook_secret(),
                "allowed_updates": ["message"],
            },
            timeout=15,
        )
        data = resp.json()
        return bool(data.get("ok")), str(data.get("description") or data)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)
