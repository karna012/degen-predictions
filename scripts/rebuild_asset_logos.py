"""Rebuild stock logos from company domains + Wikipedia; crypto from CoinGecko.

FMP symbol images are often wrong for India tickers (e.g. INDIGO.NS -> Flywire).
We map each ticker to an official website domain (from the prior Clearbit map,
with manual fixes), then pull logos from cdn.tickerlogos.com / icon.horse /
Wikipedia summary thumbnails, verify HTTP image responses, and write:
  - ranked_assets.csv logo_url
  - webapp/static/asset-logos/{safe_ticker}.png (self-hosted, no mismatch CDN)
  - asset_logo_cache.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))
import asset_logos  # noqa: E402

CSV_PATH = ROOT / "data" / "investment_scoring" / "ranked_assets.csv"
BACKUP_CLEARBIT = ROOT.parent / "investment_scoring" / "output" / "ranked_assets_webapp_restore.csv"
# fall back to investment_scoring ranked which still has clearbit domains
ALT_CLEARBIT = ROOT.parent / "investment_scoring" / "output" / "ranked_assets.csv"
STATIC_DIR = ROOT / "webapp" / "static" / "asset-logos"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "DegenPredictionsLogoRebuild/1.0 (research; logos)"

# Manual domain corrections (Clearbit/domain scrapers get these wrong).
DOMAIN_OVERRIDES = {
    "ETERNAL.NS": "zomato.com",          # Eternal Ltd = Zomato rebrand
    "TMPV.NS": "tatamotors.com",         # Tata Motors PV rename
    "INDIGO.NS": "goindigo.in",
    "M&M.NS": "mahindra.com",
    "HDFCBANK.NS": "hdfcbank.com",
    "KOTAKBANK.NS": "kotak.com",
    "SBIN.NS": "sbi.co.in",
    "ICICIBANK.NS": "icicibank.com",
    "AXISBANK.NS": "axisbank.com",
    "BAJAJ-AUTO.NS": "bajajauto.com",
}

# Wikipedia page titles for tickers where domain CDNs serve the wrong brand.
WIKI_PAGES = {
    "INDIGO.NS": "IndiGo",
    "TRENT.NS": "Trent_Limited",
    "M&M.NS": "Mahindra_%26_Mahindra",
    "TMPV.NS": "Tata_Motors",
    "ETERNAL.NS": "Zomato",
    "RELIANCE.NS": "Reliance_Industries",
    "JIOFIN.NS": "Jio_Financial_Services",
    "TCS.NS": "Tata_Consultancy_Services",
    "INFY.NS": "Infosys",
    "WIPRO.NS": "Wipro",
    "ITC.NS": "ITC_Limited",
    "HINDUNILVR.NS": "Hindustan_Unilever",
    "BHARTIARTL.NS": "Bharti_Airtel",
    "MARUTI.NS": "Maruti_Suzuki",
    "TATAMOTORS.NS": "Tata_Motors",
    "SUNPHARMA.NS": "Sun_Pharmaceutical",
    "ASIANPAINT.NS": "Asian_Paints",
    "NESTLEIND.NS": "Nestlé_India",
    "TITAN.NS": "Titan_Company",
    "ULTRACEMCO.NS": "UltraTech_Cement",
    "LT.NS": "Larsen_%26_Toubro",
    "ADANIENT.NS": "Adani_Enterprises",
    "ADANIPORTS.NS": "Adani_Ports_%26_SEZ",
    "COALINDIA.NS": "Coal_India",
    "ONGC.NS": "Oil_and_Natural_Gas_Corporation",
    "NTPC.NS": "NTPC_Limited",
    "POWERGRID.NS": "Power_Grid_Corporation_of_India",
    "SBIN.NS": "State_Bank_of_India",
    "HDFCBANK.NS": "HDFC_Bank",
    "ICICIBANK.NS": "ICICI_Bank",
    "AXISBANK.NS": "Axis_Bank",
    "KOTAKBANK.NS": "Kotak_Mahindra_Bank",
    "BAJFINANCE.NS": "Bajaj_Finance",
    "BAJAJFINSV.NS": "Bajaj_Finserv",
    "BAJAJ-AUTO.NS": "Bajaj_Auto",
    "HCLTECH.NS": "HCLTech",
    "TECHM.NS": "Tech_Mahindra",
    "CIPLA.NS": "Cipla",
    "DRREDDY.NS": "Dr._Reddy%27s_Laboratories",
    "APOLLOHOSP.NS": "Apollo_Hospitals",
    "TATASTEEL.NS": "Tata_Steel",
    "JSWSTEEL.NS": "JSW_Steel",
    "HINDALCO.NS": "Hindalco_Industries",
    "GRASIM.NS": "Grasim_Industries",
    "TATACONSUM.NS": "Tata_Consumer_Products",
    "EICHERMOT.NS": "Eicher_Motors",
    "HEROMOTOCO.NS": "Hero_MotoCorp",
    "BEL.NS": "Bharat_Electronics",
    "HDFCLIFE.NS": "HDFC_Life",
    "SBILIFE.NS": "SBI_Life_Insurance_Company",
    "SHRIRAMFIN.NS": "Shriram_Finance",
    "MAXHEALTH.NS": "Max_Healthcare",
}


def safe_ticker_filename(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ticker.strip())


def domain_from_clearbit(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    if "logo.clearbit.com/" not in url:
        return None
    host = url.split("logo.clearbit.com/", 1)[1].strip().strip("/")
    return host.split("/")[0].lower() or None


def load_domain_map(tickers: list[str]) -> dict[str, str]:
    domains = dict(DOMAIN_OVERRIDES)
    for path in (BACKUP_CLEARBIT, ALT_CLEARBIT, CSV_PATH):
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, usecols=lambda c: c in {"ticker", "logo_url", "asset_class"})
        except Exception:
            continue
        if "logo_url" not in df.columns:
            continue
        for row in df.itertuples(index=False):
            t = str(getattr(row, "ticker", ""))
            if t in domains:
                continue
            dom = domain_from_clearbit(getattr(row, "logo_url", None))
            if dom:
                domains[t] = dom
    # Fill remaining US tickers with lowercase.com guess only if missing — skip
    return domains


def wiki_thumbnail(page: str) -> str | None:
    """Prefer the summary thumbnail (logo/icon) over originalimage (often a photo)."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{page}"
    try:
        r = SESSION.get(url, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        thumb = (data.get("thumbnail") or {}).get("source")
        if thumb:
            # bump size a bit for retina cards
            return thumb.replace("/330px-", "/500px-").replace("/320px-", "/500px-")
        return (data.get("originalimage") or {}).get("source")
    except Exception:
        return None


def fetch_bytes(url: str) -> bytes | None:
    try:
        r = SESSION.get(url, timeout=20, allow_redirects=True)
        if r.status_code != 200:
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        body = r.content
        if len(body) < 200:
            return None
        if "image" in ctype or body[:8] == b"\x89PNG\r\n\x1a\n" or body[:3] == b"\xff\xd8\xff" or body[:4] == b"GIF8" or body[:4] == b"<svg" or b"<svg" in body[:200].lower():
            # reject tiny placeholder icons (<300B often letter tiles)
            if len(body) < 300 and "svg" not in ctype:
                return None
            return body
    except Exception:
        return None
    return None


def candidate_urls(ticker: str, domain: str | None) -> list[str]:
    urls: list[str] = []
    if ticker in WIKI_PAGES:
        thumb = wiki_thumbnail(WIKI_PAGES[ticker])
        if thumb:
            urls.append(thumb)
    if domain:
        urls.append(f"https://cdn.tickerlogos.com/{domain}")
        urls.append(f"https://icon.horse/icon/{domain}")
        urls.append(f"https://www.google.com/s2/favicons?domain={domain}&sz=128")
    # FMP last — often wrong for India, but ok for many US names
    t_enc = quote(ticker, safe=".-")
    urls.append(f"https://images.financialmodelingprep.com/symbol/{t_enc}.png")
    if ticker.endswith(".NS"):
        bare = ticker[:-3]
        urls.append(f"https://images.financialmodelingprep.com/symbol/{quote(bare, safe='.-')}.png")
        # renamed Tata Motors
        if ticker == "TMPV.NS":
            urls.insert(0, "https://images.financialmodelingprep.com/symbol/TATAMOTORS.NS.png")
            tw = wiki_thumbnail("Tata_Motors")
            if tw:
                urls.insert(0, tw)
    # de-dupe
    seen = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def resolve_and_store(ticker: str, domain: str | None) -> str | None:
    """Download best logo to static/; return site-relative URL."""
    fname = safe_ticker_filename(ticker) + ".png"
    dest = STATIC_DIR / fname
    public = f"/static/asset-logos/{fname}"

    for url in candidate_urls(ticker, domain):
        data = fetch_bytes(url)
        if not data:
            continue
        # Prefer raster; if SVG, still save as .svg path? keep .png name only for raster
        if data[:4] == b"<svg" or b"<svg" in data[:200].lower():
            fname_svg = safe_ticker_filename(ticker) + ".svg"
            dest_svg = STATIC_DIR / fname_svg
            dest_svg.write_bytes(data)
            return f"/static/asset-logos/{fname_svg}"
        dest.write_bytes(data)
        return public
    return None


def refresh_crypto(coin_ids: list[str]) -> dict[str, str]:
    cache = asset_logos._load_cache()
    for cid in coin_ids:
        cache.pop(cid, None)
    cache = asset_logos.fetch_coingecko_logos(coin_ids, cache=cache)
    missing = [c for c in coin_ids if not asset_logos.normalize_logo_url(cache.get(c))]
    for i, cid in enumerate(missing):
        try:
            resp = SESSION.get(
                f"https://api.coingecko.com/api/v3/coins/{cid}",
                params={
                    "localization": "false", "tickers": "false", "market_data": "false",
                    "community_data": "false", "developer_data": "false",
                },
                timeout=20,
            )
            if resp.status_code == 200:
                image = resp.json().get("image") or {}
                url = image.get("large") or image.get("small")
                if asset_logos.normalize_logo_url(url):
                    cache[cid] = url
            elif resp.status_code == 429:
                time.sleep(6)
        except Exception as exc:
            print("crypto detail fail", cid, exc)
        time.sleep(1.2)
    # Also mirror crypto images into static for stability
    out_map = {}
    for cid in coin_ids:
        url = asset_logos.normalize_logo_url(cache.get(cid))
        if not url:
            continue
        data = fetch_bytes(url)
        if data:
            fname = safe_ticker_filename(cid) + (".jpg" if b"JFIF" in data[:32] or data[:3] == b"\xff\xd8\xff" else ".png")
            (STATIC_DIR / fname).write_bytes(data)
            out_map[cid] = f"/static/asset-logos/{fname}"
            cache[cid] = out_map[cid]
        else:
            out_map[cid] = url
    asset_logos._save_cache(cache)
    return out_map


def main():
    df = pd.read_csv(CSV_PATH)
    stock_tickers = sorted(
        df.loc[df["asset_class"] != "crypto", "ticker"].dropna().astype(str).unique().tolist()
    )
    crypto_ids = sorted(
        df.loc[df["asset_class"] == "crypto", "ticker"].dropna().astype(str).unique().tolist()
    )
    domains = load_domain_map(stock_tickers)

    print(f"Crypto refresh ({len(crypto_ids)})...")
    crypto_map = refresh_crypto(crypto_ids)
    print(f"  crypto logos: {len(crypto_map)}/{len(crypto_ids)}")

    print(f"Stock resolve+host ({len(stock_tickers)})...")
    stock_map: dict[str, str] = {}
    fails: list[str] = []

    def job(t: str):
        return t, resolve_and_store(t, domains.get(t))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(job, t) for t in stock_tickers]
        done = 0
        for fut in as_completed(futs):
            t, url = fut.result()
            done += 1
            if url:
                stock_map[t] = url
            else:
                fails.append(t)
            if done % 50 == 0 or done == len(futs):
                print(f"  {done}/{len(futs)} ok={len(stock_map)} fail={len(fails)}")

    print(f"Stocks OK {len(stock_map)}/{len(stock_tickers)}; fail={fails}")

    def pick(row):
        t = str(row["ticker"])
        if str(row.get("asset_class")) == "crypto":
            return crypto_map.get(t)
        return stock_map.get(t)

    df["logo_url"] = df.apply(pick, axis=1)
    df.to_csv(CSV_PATH, index=False)

    cache = asset_logos._load_cache()
    for t, url in stock_map.items():
        cache[f"stock:{t}"] = url
    for cid, url in crypto_map.items():
        cache[cid] = url
    asset_logos._save_cache(cache)

    report = {
        "stocks_ok": len(stock_map),
        "stocks_fail": fails,
        "crypto_ok": len(crypto_map),
        "crypto_fail": [c for c in crypto_ids if c not in crypto_map],
        "static_files": len(list(STATIC_DIR.glob("*"))),
    }
    report_path = ROOT / "data" / "investment_scoring" / "logo_verify_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Wrote", CSV_PATH)
    print("Wrote", report_path)
    print("Static logos:", report["static_files"])


if __name__ == "__main__":
    main()
