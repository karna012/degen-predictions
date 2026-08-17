"""Live black-box smoke against production (route + logo + API health)."""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = "https://degenpredictions.com"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "DegenPredictionsFullTestReport/1.0"
TIMEOUT = 25

# Public pages / APIs that should stay green.
ROUTES = [
    ("GET", "/", 200),
    ("GET", "/faq", 200),
    ("GET", "/sports", 200),
    ("GET", "/sports/soccer", 200),
    ("GET", "/sports/cricket", 200),
    ("GET", "/mlb/", 200),
    ("GET", "/npb/", 200),
    ("GET", "/todays-picks", 200),
    ("GET", "/stocks", 200),
    ("GET", "/stocks/us", 200),
    ("GET", "/stocks/india", 200),
    ("GET", "/crypto", 200),
    ("GET", "/llms.txt", 200),
    ("GET", "/robots.txt", 200),
    ("GET", "/sitemap.xml", 200),
]

LOGOS = [
    "/static/asset-logos/AAPL.png",
    "/static/asset-logos/bitcoin.png",
    "/static/asset-logos/ethereum.png",
    "/static/asset-logos/TRENT.NS.png",
    "/static/asset-logos/INDIGO.NS.png",
    "/static/asset-logos/M_M.NS.png",
    "/static/asset-logos/TMPV.NS.png",
    "/static/asset-logos/ETERNAL.NS.png",
    "/static/ui-pro.css",
    "/static/mobile.css",
]


def check(method: str, path: str, expect: int) -> dict:
    url = BASE + path
    t0 = time.perf_counter()
    try:
        r = SESSION.request(method, url, timeout=TIMEOUT, allow_redirects=True)
        ms = int((time.perf_counter() - t0) * 1000)
        ok = r.status_code == expect
        ctype = (r.headers.get("content-type") or "")[:60]
        detail = ""
        if path.startswith("/static/asset-logos/") and ok:
            ok = "image" in ctype or path.endswith(".svg")
            if not ok:
                detail = f"bad content-type={ctype}"
        if path in ("/stocks/us", "/crypto", "/stocks/india") and ok:
            body = r.text.lower()
            if "logo_url" in body or "asset-logo" in body or "/static/asset-logos/" in body or "asset-monogram" in body:
                pass
            # soft check: page should mention rankings content
            if "predicted" not in body and "rank" not in body and "beat" not in body:
                detail = "page missing expected ranking keywords"
                ok = False
        return {
            "path": path, "status": r.status_code, "expect": expect,
            "ok": ok, "ms": ms, "ctype": ctype, "detail": detail,
        }
    except Exception as exc:
        return {
            "path": path, "status": None, "expect": expect,
            "ok": False, "ms": int((time.perf_counter() - t0) * 1000),
            "ctype": "", "detail": str(exc),
        }


def main():
    jobs = [(m, p, e) for m, p, e in ROUTES] + [("GET", p, 200) for p in LOGOS]
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(check, *j) for j in jobs]
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: r["path"])
    failed = [r for r in results if not r["ok"]]
    report = {
        "base": BASE,
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failures": failed,
        "results": results,
    }
    out = "data/test_reports/live_blackbox_smoke.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"LIVE SMOKE {report['passed']}/{report['total']} passed")
    for r in failed:
        print(f"  FAIL {r['path']} status={r['status']} detail={r['detail']}")
    for r in results:
        mark = "OK" if r["ok"] else "FAIL"
        print(f"  {mark:4} {r['status']} {r['ms']:5}ms  {r['path']}")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
