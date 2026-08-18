"""Verified search-crawler detection for the sports unlock gate.

Matching the User-Agent string alone is not enough -- anyone can set their
browser's UA to "Googlebot" and walk straight through the gate. Google and
Bing both document the same fix: reverse-DNS the request IP, confirm the
hostname is on the crawler's real domain, then forward-DNS that hostname
and confirm it round-trips back to the same IP (rules out a spoofed PTR
record). Only requests that pass this get the un-gated page; everyone else
(including UA-spoofers) goes through the normal join-gate.

Fails closed: any lookup error/timeout counts as "not verified", i.e. the
visitor gets the gate. Worst case a real crawler occasionally sees the gate
page instead of content -- much better than a paywall anyone can bypass by
changing their User-Agent.
"""
from __future__ import annotations

import socket
import time

# UA substring -> allowed reverse-DNS suffixes for that crawler family.
_CRAWLER_UA_DOMAINS = {
    "googlebot": (".googlebot.com", ".google.com"),
    "bingbot": (".search.msn.com",),
    "duckduckbot": (".duckduckgo.com",),
}

_DNS_TIMEOUT_SECONDS = 2.0
_CACHE_TTL_SECONDS = 24 * 60 * 60
_cache: dict[str, tuple[bool, float]] = {}


def _looks_like_crawler_ua(user_agent: str) -> str | None:
    ua = (user_agent or "").strip().lower()
    for needle in _CRAWLER_UA_DOMAINS:
        if needle in ua:
            return needle
    return None


def _verify_ip(ip: str, allowed_suffixes: tuple[str, ...]) -> bool:
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_DNS_TIMEOUT_SECONDS)
    try:
        hostname, _aliases, _addrs = socket.gethostbyaddr(ip)
        hostname = hostname.lower().rstrip(".")
        if not any(hostname == s.lstrip(".") or hostname.endswith(s) for s in allowed_suffixes):
            return False
        # Round-trip: forward-resolve the claimed hostname and confirm it
        # points back at the same IP (rules out a spoofed/stale PTR record).
        forward_ip = socket.gethostbyname(hostname)
        return forward_ip == ip
    except (socket.herror, socket.gaierror, socket.timeout, OSError):
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def is_verified_crawler(user_agent: str, ip: str) -> bool:
    """True only for a request that is both UA-claiming and DNS-provably a
    known search crawler. Cached per-IP since a real crawler hits from a
    fairly stable IP pool and repeats constantly."""
    family = _looks_like_crawler_ua(user_agent)
    if not family or not ip:
        return False
    now = time.time()
    cached = _cache.get(ip)
    if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
        return cached[0]
    verified = _verify_ip(ip, _CRAWLER_UA_DOMAINS[family])
    _cache[ip] = (verified, now)
    return verified
