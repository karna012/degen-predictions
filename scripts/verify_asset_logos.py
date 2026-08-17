"""Verify and rebuild logo URLs for every stock + crypto in ranked_assets.csv."""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webapp"))

import asset_logos  # noqa: E402

CSV_PATH = os.path.join(ROOT, "data", "investment_scoring", "ranked_assets.csv")
CACHE_PATH = asset_logos.LOGO_CACHE_PATH
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "DegenPredictionsLogoVerify/1.0"


def image_ok(url: str) -> bool:
    if not url:
        return False
    try:
        r = SESSION.get(url, timeout=12, stream=True)
        if r.status_code != 200:
            return False
        ctype = (r.headers.get("content-type") or "").lower()
        if "image" not in ctype and "octet-stream" not in ctype:
            # some CDNs omit content-type; accept if body looks like image magic
            chunk = next(r.iter_content(16), b"")
            return chunk[:3] == b"\xff\xd8\xff" or chunk[:8] == b"\x89PNG\r\n\x1a\n" or chunk[:4] == b"GIF8"
        # drain a few bytes to confirm not empty HTML error page
        chunk = next(r.iter_content(64), b"")
        if b"<html" in chunk.lower() or b"<!doctype" in chunk.lower():
            return False
        return len(chunk) > 0
    except Exception:
        return False


def candidate_stock_urls(ticker: str) -> list[str]:
    t = ticker.strip()
    urls = [asset_logos.FMP_SYMBOL_LOGO.format(ticker=t)]
    if t.endswith(".NS") or t.endswith(".BO"):
        bare = t.rsplit(".", 1)[0]
        urls.append(asset_logos.FMP_SYMBOL_LOGO.format(ticker=bare))
        urls.append(asset_logos.FMP_SYMBOL_LOGO.format(ticker=f"{bare}.NSE"))
    # US common share-class dots: BRK.B -> BRK-B used by some CDNs
    if "." in t and not t.endswith((".NS", ".BO")):
        urls.append(asset_logos.FMP_SYMBOL_LOGO.format(ticker=t.replace(".", "-")))
    # de-dupe preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def resolve_stock_logo(ticker: str) -> str | None:
    for url in candidate_stock_urls(ticker):
        if image_ok(url):
            return url
    return None


def refresh_crypto_logos(coin_ids: list[str]) -> dict:
    """Force-refresh every coin image from CoinGecko (ignore stale cache)."""
    cache = asset_logos._load_cache()
    # Drop existing entries for these ids so fetch pulls fresh
    for cid in coin_ids:
        cache.pop(cid, None)
    # Also try /coins/{id} for any batch misses (more reliable single-id image)
    cache = asset_logos.fetch_coingecko_logos(coin_ids, cache=cache)
    missing = [c for c in coin_ids if not asset_logos.normalize_logo_url(cache.get(c))]
    for i, cid in enumerate(missing):
        try:
            resp = SESSION.get(
                f"https://api.coingecko.com/api/v3/coins/{cid}",
                params={"localization": "false", "tickers": "false",
                        "market_data": "false", "community_data": "false",
                        "developer_data": "false"},
                timeout=20,
            )
            if resp.status_code == 200:
                image = (resp.json().get("image") or {})
                url = image.get("large") or image.get("small") or image.get("thumb")
                if asset_logos.normalize_logo_url(url):
                    cache[cid] = url
            elif resp.status_code == 429:
                time.sleep(5)
        except Exception as exc:
            print(f"  coin detail failed {cid}: {exc}")
        if i + 1 < len(missing):
            time.sleep(1.3)
    asset_logos._save_cache(cache)
    return cache


def main():
    df = pd.read_csv(CSV_PATH)
    stocks = (
        df[df["asset_class"] != "crypto"][["ticker", "asset_class"]]
        .drop_duplicates("ticker")
        .sort_values(["asset_class", "ticker"])
    )
    crypto_ids = sorted(df.loc[df["asset_class"] == "crypto", "ticker"].dropna().astype(str).unique())

    print(f"Refreshing {len(crypto_ids)} crypto logos from CoinGecko...")
    crypto_cache = refresh_crypto_logos(crypto_ids)
    crypto_ok = {cid: crypto_cache.get(cid) for cid in crypto_ids
                 if asset_logos.normalize_logo_url(crypto_cache.get(cid))}
    crypto_bad = [cid for cid in crypto_ids if cid not in crypto_ok]
    print(f"  crypto OK {len(crypto_ok)} / {len(crypto_ids)}")
    if crypto_bad:
        print(f"  crypto still missing: {crypto_bad}")

    # Verify crypto URLs actually load
    print("Verifying crypto image URLs...")
    crypto_broken = []
    for cid, url in list(crypto_ok.items()):
        if not image_ok(url):
            crypto_broken.append(cid)
            crypto_ok.pop(cid, None)
    if crypto_broken:
        print(f"  crypto URL broken ({len(crypto_broken)}): {crypto_broken[:20]}")

    print(f"Resolving {len(stocks)} stock logos (FMP variants)...")
    stock_map: dict[str, str] = {}
    stock_fail: list[str] = []

    def _job(ticker: str):
        return ticker, resolve_stock_logo(ticker)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(_job, str(t)) for t in stocks["ticker"]]
        done = 0
        for fut in as_completed(futs):
            ticker, url = fut.result()
            done += 1
            if url:
                stock_map[ticker] = url
            else:
                stock_fail.append(ticker)
            if done % 50 == 0 or done == len(futs):
                print(f"  stocks checked {done}/{len(futs)} ok={len(stock_map)} fail={len(stock_fail)}")

    print(f"Stock logos OK {len(stock_map)} / {len(stocks)}; fail={len(stock_fail)}")
    if stock_fail:
        print("  failures:", ", ".join(stock_fail[:40]), ("..." if len(stock_fail) > 40 else ""))

    # Write verified URLs back into CSV
    def pick_logo(row):
        t = str(row["ticker"])
        if str(row.get("asset_class", "")) == "crypto":
            return crypto_ok.get(t)
        return stock_map.get(t)

    df["logo_url"] = df.apply(pick_logo, axis=1)
    df.to_csv(CSV_PATH, index=False)
    print(f"Wrote {CSV_PATH}")

    # Persist stock overrides into cache file under stock:TICKER keys for runtime
    cache = asset_logos._load_cache()
    for t, url in stock_map.items():
        cache[f"stock:{t}"] = url
    for cid, url in crypto_ok.items():
        cache[cid] = url
    # drop dead clearbit leftovers if any
    for k, v in list(cache.items()):
        if isinstance(v, str) and v.startswith(asset_logos.CLEARBIT_PREFIX):
            del cache[k]
    asset_logos._save_cache(cache)
    print(f"Updated {CACHE_PATH} ({len(cache)} entries)")

    report = {
        "stocks_ok": len(stock_map),
        "stocks_fail": stock_fail,
        "crypto_ok": len(crypto_ok),
        "crypto_fail": crypto_bad + crypto_broken,
    }
    report_path = os.path.join(ROOT, "data", "investment_scoring", "logo_verify_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
