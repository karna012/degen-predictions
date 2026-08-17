"""Append-only admin audit log (JSONL)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
LOG_PATH = Path(os.environ.get("ADMIN_AUDIT_PATH") or (REPO / "data" / "admin" / "audit.jsonl"))
MAX_LINES_READ = 400


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log_event(action: str, *, ok: bool = True, detail: str = "", meta: dict | None = None,
              ip: str = "", actor: str = "admin") -> None:
    row = {
        "ts": _utcnow(),
        "action": (action or "unknown")[:80],
        "ok": bool(ok),
        "actor": (actor or "admin")[:80],
        "ip": (ip or "")[:80],
        "detail": (detail or "")[:400],
        "meta": meta or {},
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent(limit: int = 80) -> list[dict[str, Any]]:
    if not LOG_PATH.is_file():
        return []
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-MAX_LINES_READ:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return list(reversed(out[-limit:]))
