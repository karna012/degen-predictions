"""Investment board helpers — stocks/crypto ranked CSV + admin prefs."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CSV_PATH = REPO / "data" / "investment_scoring" / "ranked_assets.csv"
CSV_BACKUP_DIR = REPO / "data" / "investment_scoring" / "backups"


def markets_enabled(key: str) -> bool:
    try:
        import source_settings
        return bool(source_settings.feed_preference(key).get("enabled", True))
    except Exception:
        return True


def preferred_providers(key: str) -> dict[str, str]:
    try:
        import source_settings
        pref = source_settings.feed_preference(key)
        return {
            "board": (pref.get("fixtures_primary") or "").strip(),
            "board_fallback": (pref.get("fixtures_fallback") or "").strip(),
            "quotes": (pref.get("results_primary") or "").strip(),
            "quotes_fallback": (pref.get("results_fallback") or "").strip(),
        }
    except Exception:
        return {}


def provider_chain(key: str, *, role: str = "quotes") -> list[str]:
    """Ordered provider ids for board (fixtures) or quotes (results)."""
    prefs = preferred_providers(key)
    if role == "board":
        ordered = [prefs.get("board"), prefs.get("board_fallback")]
    else:
        ordered = [prefs.get("quotes"), prefs.get("quotes_fallback")]
    out: list[str] = []
    for pid in ordered:
        p = (pid or "").strip()
        if p and p not in out:
            out.append(p)
    return out


def allow_live_coingecko() -> bool:
    """True when admin still wants CoinGecko for crypto logos/prices."""
    if not markets_enabled("crypto"):
        return False
    chain = provider_chain("crypto", role="quotes") + provider_chain("crypto", role="board")
    return "coingecko" in chain


def allow_fmp_stock_logos() -> bool:
    """True when FMP may be used for stock logos."""
    if not markets_enabled("stocks"):
        return False
    chain = provider_chain("stocks", role="quotes") + provider_chain("stocks", role="board")
    if "fmp" in chain:
        return True
    # Yahoo/TradingView don't serve logos — keep FMP CDN unless board is CSV-only.
    return bool(chain) and chain != ["ranked_csv"]


def csv_snapshot() -> dict[str, Any]:
    if not CSV_PATH.is_file():
        return {
            "exists": False,
            "path": str(CSV_PATH),
            "detail": "Missing ranked_assets.csv — copy from investment_scoring pipeline",
        }
    try:
        import pandas as pd
        df = pd.read_csv(CSV_PATH)
        age_h = (time.time() - CSV_PATH.stat().st_mtime) / 3600.0
        ac = df["asset_class"].astype(str) if "asset_class" in df.columns else None
        stocks = int(ac.str.endswith("stocks").sum()) if ac is not None else 0
        crypto = int((ac == "crypto").sum()) if ac is not None else 0
        classes = sorted({str(x) for x in (ac.unique() if ac is not None else [])})
        return {
            "exists": True,
            "path": str(CSV_PATH),
            "rows": int(len(df)),
            "stocks_rows": stocks,
            "crypto_rows": crypto,
            "asset_classes": classes[:12],
            "age_hours": round(age_h, 1),
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(CSV_PATH.stat().st_mtime)),
            "stale": age_h > 24 * 45,
        }
    except Exception as exc:
        return {"exists": True, "path": str(CSV_PATH), "error": str(exc)[:160]}


def clear_rankings_dataframe_cache() -> str:
    """Drop webapp cached_dataframe entry for investment rankings if present."""
    try:
        import app as app_module
        cache = getattr(app_module, "_dataframe_cache", None)
        store = getattr(cache, "cache", None)
        if isinstance(store, dict):
            drop = [k for k in list(store.keys()) if isinstance(k, tuple) and k and k[0] == "investment_rankings"]
            for k in drop:
                store.pop(k, None)
            if drop:
                return f"cleared {len(drop)} investment_rankings cache entr{'y' if len(drop)==1 else 'ies'}"
            return "investment_rankings cache already empty"
    except Exception as exc:
        return f"cache clear skipped: {exc}"[:120]
    if CSV_PATH.is_file():
        return "no in-memory cache (CSV still on disk)"
    return "no cache / csv missing"


def replace_ranked_csv(upload_bytes: bytes, *, filename: str = "ranked_assets.csv") -> dict[str, Any]:
    """Validate and atomically replace ranked_assets.csv (with timestamped backup)."""
    if not upload_bytes:
        return {"ok": False, "error": "empty upload"}
    if len(upload_bytes) > 40 * 1024 * 1024:
        return {"ok": False, "error": "file too large (40MB max)"}
    name = (filename or "").lower()
    if name and not name.endswith(".csv"):
        return {"ok": False, "error": "expected a .csv file"}

    try:
        import pandas as pd
        from io import BytesIO
        df = pd.read_csv(BytesIO(upload_bytes))
    except Exception as exc:
        return {"ok": False, "error": f"CSV parse failed: {exc}"[:160]}

    if df.empty or len(df) < 5:
        return {"ok": False, "error": "CSV needs at least 5 data rows"}
    cols = {str(c).lower() for c in df.columns}
    if "ticker" not in cols:
        return {"ok": False, "error": "CSV must include a ticker column"}
    if "asset_class" not in cols:
        return {"ok": False, "error": "CSV must include an asset_class column"}

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    CSV_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH.is_file():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = CSV_BACKUP_DIR / f"ranked_assets.{stamp}.csv"
        try:
            shutil.copy2(CSV_PATH, backup)
        except OSError as exc:
            return {"ok": False, "error": f"backup failed: {exc}"[:120]}

    tmp = CSV_PATH.with_suffix(".csv.tmp")
    try:
        tmp.write_bytes(upload_bytes)
        os.replace(tmp, CSV_PATH)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "error": f"write failed: {exc}"[:120]}

    cache_msg = clear_rankings_dataframe_cache()
    snap = csv_snapshot()
    return {
        "ok": True,
        "detail": f"replaced CSV · {snap.get('rows', '?')} rows · {cache_msg}",
        "snapshot": snap,
    }
