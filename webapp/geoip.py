"""IP -> rough location, via ip-api.com's free tier (no key, HTTP only,
rate-limited to ~45 req/min per source IP). Cached indefinitely in-memory
per IP since a given address's approximate geolocation doesn't meaningfully
change between admin-dashboard page loads -- avoids re-hitting the rate
limit on every request for the same recurring visitor IPs.

Fails soft: a lookup failure or rate-limit hit returns "Unknown" rather
than breaking the dashboard.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

_cache = {}
PRIVATE_PREFIXES = ("127.", "10.", "192.168.", "::1", "localhost")


def _is_private(ip):
    return not ip or any(ip.startswith(p) for p in PRIVATE_PREFIXES) or ip.startswith("172.16.")


def get_location(ip):
    """Returns a short "City, Country" string, "Private/Local", or
    "Unknown" -- never raises."""
    if _is_private(ip):
        return "Private/Local"
    if ip in _cache:
        return _cache[ip]
    try:
        resp = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,city"}, timeout=5,
        )
        data = resp.json()
        if data.get("status") == "success":
            city = data.get("city") or ""
            country = data.get("country") or ""
            label = ", ".join(p for p in (city, country) if p) or "Unknown"
        else:
            label = "Unknown"
    except Exception as exc:
        logger.warning("Geolocation lookup failed for %s: %s", ip, exc)
        label = "Unknown"
    _cache[ip] = label
    return label


def prefetch_locations(ips, max_workers=10):
    """Resolves every not-yet-cached IP in `ips` in parallel before the
    caller starts rendering -- sequential lookups here caused the exact
    same kind of slow-page-load problem the NPB pitcher-name lookups did
    earlier in this project (see npb_fixtures.py), same fix applies."""
    uncached = [ip for ip in set(ips) if ip not in _cache and not _is_private(ip)]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(get_location, uncached))
