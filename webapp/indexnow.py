"""IndexNow client — notify Bing (and peers) when URLs change.

Key file must be served at:
  https://degenpredictions.com/<INDEXNOW_KEY>.txt
with body == the key (UTF-8). See https://www.indexnow.org/documentation

Wired from:
  - daily_digest.run_daily_job (after slate write)
  - post_event_refresh (when something settled)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DEFAULT_KEY = "84e8fa3329404e81b2aba483549d03f4"
SITE_HOST = "degenpredictions.com"
SITE_ORIGIN = f"https://{SITE_HOST}"
ENDPOINT = "https://api.indexnow.org/indexnow"

# Cap per IndexNow request (Bing allows large batches; keep modest).
MAX_URLS = 64


def indexnow_enabled() -> bool:
    raw = (os.environ.get("INDEXNOW_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def indexnow_key() -> str:
    return (os.environ.get("INDEXNOW_KEY") or DEFAULT_KEY).strip() or DEFAULT_KEY


def key_location(key: str | None = None) -> str:
    k = key or indexnow_key()
    return f"{SITE_ORIGIN}/{k}.txt"


def default_url_list() -> list[str]:
    """High-value public URLs worth pinging after deploy / slate refresh."""
    urls = [
        f"{SITE_ORIGIN}/",
        f"{SITE_ORIGIN}/sports",
        f"{SITE_ORIGIN}/todays-picks",
        f"{SITE_ORIGIN}/todays-picks/combos",
        f"{SITE_ORIGIN}/accuracy",
        f"{SITE_ORIGIN}/premier_league/",
        f"{SITE_ORIGIN}/premier_league/predictions-today",
        f"{SITE_ORIGIN}/la_liga/",
        f"{SITE_ORIGIN}/la_liga/predictions-today",
        f"{SITE_ORIGIN}/serie_a/",
        f"{SITE_ORIGIN}/serie_a/predictions-today",
        f"{SITE_ORIGIN}/bundesliga/",
        f"{SITE_ORIGIN}/bundesliga/predictions-today",
        f"{SITE_ORIGIN}/ligue_1/",
        f"{SITE_ORIGIN}/ligue_1/predictions-today",
        f"{SITE_ORIGIN}/mls/",
        f"{SITE_ORIGIN}/mls/predictions-today",
        f"{SITE_ORIGIN}/primeira_liga/",
        f"{SITE_ORIGIN}/primeira_liga/predictions-today",
        f"{SITE_ORIGIN}/liga_mx/",
        f"{SITE_ORIGIN}/liga_mx/predictions-today",
        f"{SITE_ORIGIN}/ipl/",
        f"{SITE_ORIGIN}/cpl/",
        f"{SITE_ORIGIN}/mlb/",
        f"{SITE_ORIGIN}/nfl/",
        f"{SITE_ORIGIN}/nba/",
        f"{SITE_ORIGIN}/tennis/",
        f"{SITE_ORIGIN}/sitemap.xml",
        f"{SITE_ORIGIN}/sitemap-leagues.xml",
        f"{SITE_ORIGIN}/sitemap-matches.xml",
    ]
    return urls


def urls_from_digest(digest: dict | None) -> list[str]:
    """Collect public board URLs touched by today's digest."""
    urls = list(default_url_list())
    if not digest:
        return urls
    for row in digest.get("league_urls") or []:
        u = (row.get("url") or "").strip()
        if u.startswith(SITE_ORIGIN):
            urls.append(u)
        elif u.startswith("/"):
            urls.append(f"{SITE_ORIGIN}{u}")
    for p in digest.get("picks") or []:
        u = (p.get("url") or "").strip()
        if u.startswith(SITE_ORIGIN):
            urls.append(u)
        elif u.startswith("/"):
            urls.append(f"{SITE_ORIGIN}{u}")
    return urls


def submit_urls(
    urls: Iterable[str] | None = None,
    *,
    key: str | None = None,
    timeout: float = 20.0,
) -> dict:
    """POST urlList to api.indexnow.org. Returns status dict (never raises)."""
    if not indexnow_enabled():
        return {"ok": False, "skipped": True, "error": "INDEXNOW_ENABLED=0", "status": None}

    k = key or indexnow_key()
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in urls if urls is not None else default_url_list():
        u = (raw or "").strip()
        if not u or u in seen:
            continue
        if not u.startswith(SITE_ORIGIN):
            continue
        seen.add(u)
        cleaned.append(u)
        if len(cleaned) >= MAX_URLS:
            break
    if not cleaned:
        return {"ok": False, "error": "no_urls", "status": None}

    payload = {
        "host": SITE_HOST,
        "key": k,
        "keyLocation": key_location(k),
        "urlList": cleaned,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "degen-predictions-indexnow/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()[:500]
        ok = int(status) in (200, 202)
        logger.info("indexnow: submitted %s url(s) → HTTP %s", len(cleaned), status)
        return {
            "ok": ok,
            "status": int(status),
            "n": len(cleaned),
            "body": raw.decode("utf-8", errors="replace"),
        }
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read()[:300].decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.warning("indexnow: HTTP %s — %s", exc.code, detail[:120])
        return {
            "ok": False,
            "status": int(exc.code),
            "n": len(cleaned),
            "error": detail or str(exc),
        }
    except Exception as exc:
        logger.warning("indexnow: failed — %s", exc)
        return {"ok": False, "status": None, "n": len(cleaned), "error": str(exc)}


def notify_slate(digest: dict | None = None, *, extra: Iterable[str] | None = None) -> dict:
    """Ping IndexNow for the daily board (safe no-op if disabled)."""
    urls = urls_from_digest(digest)
    if extra:
        urls.extend(extra)
    return submit_urls(urls)


def notify_after_settle(settle: dict[str, Any] | None) -> dict:
    """Ping IndexNow only when settle work actually graded something."""
    settle = settle or {}
    graded = (
        int(settle.get("legs_settled") or 0)
        + int(settle.get("slips_settled") or 0)
        + int(settle.get("picks_settled") or 0)
    )
    if graded <= 0:
        return {"ok": False, "skipped": True, "error": "nothing_settled", "n": 0}
    return submit_urls(
        default_url_list()
        + [
            f"{SITE_ORIGIN}/mlb/results",
            f"{SITE_ORIGIN}/ipl/results",
            f"{SITE_ORIGIN}/cpl/results",
            f"{SITE_ORIGIN}/tennis/results",
            f"{SITE_ORIGIN}/hundred/results",
        ]
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(submit_urls(), indent=2))
