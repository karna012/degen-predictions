"""Cross-process fixture cache invalidation.

Gunicorn workers and the post-event systemd job do not share in-memory
``_cache`` dicts. Touching this file lets every process drop stale scores
as soon as a settle run (or a results-page refresh) says finals changed.
"""
from __future__ import annotations

import os
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUST_PATH = os.path.join(_REPO, "data", "feedback", "live_cache_bust")


def mark_stale() -> str:
    os.makedirs(os.path.dirname(BUST_PATH), exist_ok=True)
    with open(BUST_PATH, "w", encoding="utf-8") as f:
        f.write(str(time.time()))
    return BUST_PATH


def bust_mtime() -> float:
    try:
        return os.path.getmtime(BUST_PATH)
    except OSError:
        return 0.0


def is_fresh(fetched_at: float, ttl_seconds: float) -> bool:
    if fetched_at <= bust_mtime():
        return False
    return (time.time() - fetched_at) < float(ttl_seconds)
