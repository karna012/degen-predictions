"""Resolve logo URLs for crypto + stock ranking cards.

Priority:
  1. Verified self-hosted files under /static/asset-logos/ (from rebuild_asset_logos.py)
  2. asset_logo_cache.json entries (crypto CoinGecko ids; stock:{TICKER})
  3. CoinGecko markets API for missing crypto
  4. Domain / FMP fallbacks for stocks without a cached logo

Never trust stale Clearbit URLs (CDN is dead) or blindly overwrite good
local logos with FMP (FMP India symbols are frequently mismatched).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Iterable
from urllib.parse import quote

import pandas as pd
import requests

logger = logging.getLogger(__name__)

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_CACHE_PATH = os.path.join(WEBAPP_DIR, "asset_logo_cache.json")
STATIC_LOGO_DIR = os.path.join(WEBAPP_DIR, "static", "asset-logos")
FMP_SYMBOL_LOGO = "https://images.financialmodelingprep.com/symbol/{ticker}.png"
CLEARBIT_PREFIX = "https://logo.clearbit.com/"
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

# Ticker -> official website domain (manual corrections + common India names).
STOCK_DOMAINS = {
    "ETERNAL.NS": "zomato.com",
    "TMPV.NS": "tatamotors.com",
    "INDIGO.NS": "goindigo.in",
    "M&M.NS": "mahindra.com",
    "HDFCBANK.NS": "hdfcbank.com",
    "KOTAKBANK.NS": "kotak.com",
    "SBIN.NS": "sbi.co.in",
    "ICICIBANK.NS": "icicibank.com",
    "AXISBANK.NS": "axisbank.com",
    "BAJAJ-AUTO.NS": "bajajauto.com",
}

# FMP symbol aliases when the listed ticker has no image yet.
STOCK_FMP_ALIASES = {
    "TMPV.NS": "TATAMOTORS.NS",
}


def _is_missing_logo(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none", "null"}


def normalize_logo_url(value):
    """Return a usable URL string, or None if missing/invalid."""
    if _is_missing_logo(value):
        return None
    text = str(value).strip()
    if text.startswith(CLEARBIT_PREFIX):
        return None
    if text.startswith("/static/"):
        return text
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return None


def _safe_ticker_filename(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ticker.strip())


def local_logo_url(ticker: str) -> str | None:
    """Return /static/asset-logos/... if a rebuilt file exists on disk."""
    if not ticker or not os.path.isdir(STATIC_LOGO_DIR):
        return None
    base = _safe_ticker_filename(ticker)
    for ext in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
        path = os.path.join(STATIC_LOGO_DIR, base + ext)
        if os.path.isfile(path) and os.path.getsize(path) >= 200:
            return f"/static/asset-logos/{base}{ext}"
    return None


def stock_logo_url(ticker: str, *, allow_fmp: bool | None = None) -> str | None:
    """Best-effort stock logo: local file -> cache -> domain CDN -> optional FMP."""
    if not ticker or not isinstance(ticker, str):
        return None
    t = ticker.strip()
    if not t:
        return None

    local = local_logo_url(t)
    if local:
        return local

    cache = _load_cache()
    cached = normalize_logo_url(cache.get(f"stock:{t}"))
    if cached:
        return cached

    domain = STOCK_DOMAINS.get(t)
    if domain:
        return f"https://cdn.tickerlogos.com/{domain}"

    if allow_fmp is None:
        try:
            import investment_sources
            allow_fmp = investment_sources.allow_fmp_stock_logos()
        except Exception:
            allow_fmp = True
    if not allow_fmp:
        return None

    fmp_symbol = STOCK_FMP_ALIASES.get(t, t)
    return FMP_SYMBOL_LOGO.format(ticker=quote(fmp_symbol, safe=".-"))


def _load_cache() -> dict:
    if not os.path.exists(LOGO_CACHE_PATH):
        return {}
    try:
        with open(LOGO_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not read logo cache: %s", exc)
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(LOGO_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=0, sort_keys=True)
    except Exception as exc:
        logger.warning("Could not write logo cache: %s", exc)


def fetch_coingecko_logos(coin_ids: Iterable[str], cache: dict | None = None) -> dict:
    """Batch-fetch CoinGecko image URLs; merges into and persists cache."""
    cache = dict(cache or _load_cache())
    needed = []
    seen = set()
    for coin_id in coin_ids:
        if not coin_id or not isinstance(coin_id, str):
            continue
        cid = coin_id.strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        cached = cache.get(cid)
        if normalize_logo_url(cached):
            continue
        needed.append(cid)

    if not needed:
        return cache

    headers = {}
    try:
        import source_settings
        key = (
            source_settings.api_key("coingecko_api_key")
            or source_settings.secret_value("COINGECKO_API_KEY")
        )
        if key:
            headers["x-cg-demo-api-key"] = key
    except Exception:
        pass

    for i in range(0, len(needed), 100):
        batch = needed[i : i + 100]
        try:
            resp = requests.get(
                COINGECKO_MARKETS,
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(batch),
                    "per_page": 250,
                    "page": 1,
                },
                headers=headers or None,
                timeout=20,
            )
            resp.raise_for_status()
            for coin in resp.json():
                image = coin.get("image")
                cid = coin.get("id")
                if cid and normalize_logo_url(image):
                    cache[cid] = image
        except Exception as exc:
            logger.warning("CoinGecko logo batch failed (%d ids): %s", len(batch), exc)
        if i + 100 < len(needed):
            time.sleep(1.25)

    _save_cache(cache)
    return cache


def attach_logo_urls(df: pd.DataFrame, fetch_missing_crypto: bool = True) -> pd.DataFrame:
    """Ensure logo_url is a real URL (or None) for every ranking row."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    if "ticker" not in df.columns:
        return df

    out = df.copy()
    if "logo_url" not in out.columns:
        out["logo_url"] = None

    asset_class = (
        out["asset_class"].astype(str).str.lower()
        if "asset_class" in out.columns
        else pd.Series([""] * len(out), index=out.index)
    )
    tickers = out["ticker"].astype(str)

    crypto_mask = asset_class.eq("crypto")
    stock_mask = asset_class.isin(["us_stocks", "india_stocks"]) | asset_class.str.endswith("_stocks")

    if stock_mask.any():
        resolved = []
        for idx in out.index[stock_mask]:
            t = str(out.at[idx, "ticker"])
            existing = normalize_logo_url(out.at[idx, "logo_url"])
            local = local_logo_url(t)
            if local:
                resolved.append(local)
            elif existing and existing.startswith("/static/"):
                resolved.append(existing)
            else:
                resolved.append(stock_logo_url(t))
        out.loc[stock_mask, "logo_url"] = resolved

    if crypto_mask.any():
        existing = out.loc[crypto_mask, "logo_url"].map(normalize_logo_url)
        out.loc[crypto_mask, "logo_url"] = existing
        cache = _load_cache()
        for idx in out.index[crypto_mask]:
            if normalize_logo_url(out.at[idx, "logo_url"]):
                continue
            t = str(out.at[idx, "ticker"]).strip()
            local = local_logo_url(t)
            if local:
                out.at[idx, "logo_url"] = local
            elif normalize_logo_url(cache.get(t)):
                out.at[idx, "logo_url"] = normalize_logo_url(cache.get(t))

        still_missing = (
            tickers[crypto_mask][out.loc[crypto_mask, "logo_url"].map(normalize_logo_url).isna()]
            .dropna().astype(str).str.strip().unique().tolist()
        )
        live_ok = True
        if fetch_missing_crypto:
            try:
                import investment_sources
                live_ok = investment_sources.allow_live_coingecko()
            except Exception:
                live_ok = True
        if fetch_missing_crypto and live_ok and still_missing:
            cache = fetch_coingecko_logos(still_missing, cache=cache)
            for idx in out.index[crypto_mask]:
                if normalize_logo_url(out.at[idx, "logo_url"]):
                    continue
                t = str(out.at[idx, "ticker"]).strip()
                out.at[idx, "logo_url"] = normalize_logo_url(cache.get(t))

    other = ~(crypto_mask | stock_mask)
    if other.any():
        out.loc[other, "logo_url"] = out.loc[other, "logo_url"].map(normalize_logo_url)

    return out


def records_with_clean_logos(records: list) -> list:
    """Final pass for dict rows headed to Jinja."""
    for row in records:
        row["logo_url"] = normalize_logo_url(row.get("logo_url"))
        if row["logo_url"] is None and row.get("ticker"):
            ac = str(row.get("asset_class") or "")
            if ac.endswith("stocks"):
                row["logo_url"] = stock_logo_url(str(row["ticker"]))
            elif ac == "crypto":
                local = local_logo_url(str(row["ticker"]))
                if local:
                    row["logo_url"] = local
    return records
