"""Last-known-good live feeds.

Upstream scoreboards fail. An empty or error response must never replace a
good snapshot — that is how fixtures vanish and results look "lost".

Each named feed keeps:
  - in-memory copy (fast, per gunicorn worker)
  - atomic JSON on disk (survives restart / worker hop)
  - status for /healthz
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from typing import Any, Callable

import requests

from live_cache_bust import is_fresh

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(_REPO, "data", "live_feeds")
HEADERS = {
    # ESPN blocks many browser-like UAs from cloud IPs (403). A plain app UA
    # succeeds; soccer/other JSON hosts accept it too.
    "User-Agent": "DegenPredictions/1.0 (+https://degenpredictions.com; feed-client)",
    "Accept": "application/json,text/plain,*/*",
}
MAX_STALE_SECONDS = 36 * 60 * 60

_memory: dict[str, dict] = {}
_status: dict[str, dict] = {}


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name)[:120]


def _disk_path(name: str) -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, f"{_safe_name(name)}.json")


def _count(payload: Any, count_items: Callable[[Any], int] | None) -> int:
    if count_items is not None:
        try:
            return int(count_items(payload))
        except Exception:
            return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        events = payload.get("events")
        if isinstance(events, list):
            return len(events)
        data = payload.get("data")
        if isinstance(data, list):
            return len(data)
    return 1 if payload not in (None, {}, []) else 0


def _read_disk(name: str) -> dict | None:
    path = _disk_path(name)
    try:
        with open(path, encoding="utf-8") as fh:
            row = json.load(fh)
        if isinstance(row, dict) and "payload" in row:
            return row
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return None


def _write_disk(name: str, payload: Any, item_count: int) -> None:
    path = _disk_path(name)
    body = json.dumps(
        {
            "name": name,
            "saved_at": time.time(),
            "item_count": item_count,
            "payload": payload,
        },
        ensure_ascii=True,
        default=str,
    )
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".feed-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        logger.exception("feed_store disk write failed for %s", name)


def _set_status(name: str, **fields: Any) -> None:
    row = dict(_status.get(name) or {})
    row.update(fields)
    row["name"] = name
    row["checked_at"] = time.time()
    _status[name] = row


def snapshot() -> dict[str, dict]:
    """Public health picture — no payloads, no secrets."""
    now = time.time()
    out = {}
    for name, row in _status.items():
        saved = float(row.get("saved_at") or row.get("fetched_at") or 0)
        out[name] = {
            "ok": bool(row.get("ok")),
            "degraded": bool(row.get("degraded")),
            "hard_fail": bool(row.get("hard_fail")),
            "items": int(row.get("items") or 0),
            "age_s": int(now - saved) if saved else None,
            "reason": row.get("reason") or "",
        }
    return out


def get_json(
    name: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 8,
    retries: int = 3,
    ttl: float = 120,
    count_items: Callable[[Any], int] | None = None,
    min_items: int = 1,
) -> Any:
    """Fetch JSON; keep last-known-good on error or empty wipe."""
    now = time.time()
    mem = _memory.get(name)
    if mem and is_fresh(float(mem.get("fetched_at") or 0), ttl):
        _set_status(
            name, ok=True, degraded=False, hard_fail=False,
            items=_count(mem["payload"], count_items),
            saved_at=mem.get("fetched_at"), reason="memory",
        )
        return mem["payload"]

    last = mem
    if last is None:
        disk = _read_disk(name)
        if disk:
            last = {
                "payload": disk["payload"],
                "fetched_at": float(disk.get("saved_at") or 0),
            }
            _memory[name] = last

    last_count = _count(last["payload"], count_items) if last else 0
    last_age = (now - float(last["fetched_at"])) if last else None
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)

    last_err = None
    payload = None
    for attempt in range(max(1, int(retries))):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            time.sleep(min(0.25 * (attempt + 1), 1.0))

    if last_err is not None:
        if last is not None:
            logger.warning("feed %s fetch failed (%s); serving last-known-good", name, last_err)
            _set_status(
                name, ok=False, degraded=True, hard_fail=False,
                items=last_count, saved_at=last.get("fetched_at"),
                reason=f"fetch:{type(last_err).__name__}",
            )
            return last["payload"]
        logger.warning("feed %s fetch failed with no snapshot: %s", name, last_err)
        _set_status(
            name, ok=False, degraded=True, hard_fail=True,
            items=0, saved_at=None, reason=f"fetch:{type(last_err).__name__}",
        )
        return None

    new_count = _count(payload, count_items)
    wipe = (
        last is not None
        and last_count >= min_items
        and new_count < min_items
        and (last_age is None or last_age < MAX_STALE_SECONDS)
    )
    if wipe:
        logger.warning(
            "feed %s returned %s items (had %s); keeping last-known-good",
            name, new_count, last_count,
        )
        _set_status(
            name, ok=False, degraded=True, hard_fail=False,
            items=last_count, saved_at=last.get("fetched_at"),
            reason="empty_payload",
        )
        return last["payload"]

    _memory[name] = {"payload": payload, "fetched_at": now}
    if new_count >= min_items:
        _write_disk(name, payload, new_count)
    _set_status(
        name, ok=True, degraded=False, hard_fail=False,
        items=new_count, saved_at=now, reason="fresh",
    )
    return payload
