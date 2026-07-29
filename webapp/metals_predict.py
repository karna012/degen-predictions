"""
Metals price prediction and investment calculator. Precious and industrial
metals only -- gemstones were removed since they have no exchange-traded
spot price (no way to source real, verifiable current pricing for them).

Base prices below were last manually refreshed 2026-07-29 against live spot
prices (Kitco/APMEX for precious metals, LME for industrial metals -- see
PRICE_LAST_UPDATED). They also serve as the fallback for any metal
automation/data_fetchers.py's MetalsDataFetcher can't cover (Zinc, Nickel,
and Lithium have no reliable free live feed -- see that module's docstring
for why).

Gold, Silver, Platinum, Palladium, Copper, Aluminum, and Steel ARE refreshed
daily by that fetcher (data/metals/current_prices.json), overlaid onto the
static values below at import time here.
"""
import json
import os
from pathlib import Path
from typing import Dict

PRICE_LAST_UPDATED = "2026-07-29"

_LIVE_PRICES_PATH = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data" / "metals" / "current_prices.json"
LIVE_PRICES_LAST_FETCHED = None


def _load_live_prices():
    """Overlay daily-fetched live prices (if present) onto the static
    defaults below. Missing/corrupt file just means "no live data yet" --
    never a hard failure, since the calculator must keep working either way."""
    global LIVE_PRICES_LAST_FETCHED
    if not _LIVE_PRICES_PATH.exists():
        return {}
    try:
        with open(_LIVE_PRICES_PATH) as f:
            payload = json.load(f)
        LIVE_PRICES_LAST_FETCHED = payload.get("last_updated")
        return payload.get("prices", {})
    except Exception:
        return {}

METALS_DATA = {
    "precious": {
        "Gold": {
            "symbol": "XAU",
            "unit": "USD/oz",
            "current_price": 4036,
            "annual_volatility": 0.12,
            "annual_appreciation": 0.085,
            "description": "Yellow precious metal",
        },
        "Silver": {
            "symbol": "XAG",
            "unit": "USD/oz",
            "current_price": 57.23,
            "annual_volatility": 0.18,
            "annual_appreciation": 0.095,
            "description": "White precious metal",
        },
        "Platinum": {
            "symbol": "XPT",
            "unit": "USD/oz",
            "current_price": 1611,
            "annual_volatility": 0.15,
            "annual_appreciation": 0.065,
            "description": "Rare precious metal",
        },
        "Palladium": {
            "symbol": "XPD",
            "unit": "USD/oz",
            "current_price": 1254,
            "annual_volatility": 0.20,
            "annual_appreciation": 0.075,
            "description": "Industrial precious metal",
        },
    },
    "industrial": {
        "Copper": {
            "symbol": "HG",
            "unit": "USD/ton",
            "current_price": 13800,
            "annual_volatility": 0.16,
            "annual_appreciation": 0.075,
            "description": "Red industrial metal",
        },
        "Aluminum": {
            "symbol": "AL",
            "unit": "USD/ton",
            "current_price": 3173,
            "annual_volatility": 0.14,
            "annual_appreciation": 0.055,
            "description": "Lightweight industrial metal",
        },
        "Steel": {
            "symbol": "SI",
            "unit": "USD/ton",
            "current_price": 960,
            "annual_volatility": 0.12,
            "annual_appreciation": 0.045,
            "description": "Iron-based alloy",
        },
        "Zinc": {
            "symbol": "ZN",
            "unit": "USD/ton",
            "current_price": 3609,
            "annual_volatility": 0.15,
            "annual_appreciation": 0.065,
            "description": "Corrosion-resistant metal",
        },
        "Nickel": {
            "symbol": "NI",
            "unit": "USD/ton",
            "current_price": 17383,
            "annual_volatility": 0.18,
            "annual_appreciation": 0.085,
            "description": "Stainless steel component",
        },
        "Lithium": {
            "symbol": "LI",
            "unit": "USD/ton",
            "current_price": 21620,
            "annual_volatility": 0.22,
            "annual_appreciation": 0.15,
            "description": "Battery metal",
        },
    },
}


def get_categories():
    return list(METALS_DATA.keys())


def get_metals_by_category(category: str):
    return list(METALS_DATA.get(category, {}).keys())


def get_all_metals():
    live_prices = _load_live_prices()
    all_metals = {}
    for category, metals in METALS_DATA.items():
        for metal_name, data in metals.items():
            entry = {**data, "category": category}
            if metal_name in live_prices:
                entry["current_price"] = live_prices[metal_name]
                entry["price_is_live"] = True
            else:
                entry["price_is_live"] = False
            all_metals[metal_name] = entry
    return all_metals


def _get_cycle_multiplier(volatility: float) -> float:
    if volatility < 0.10:
        return 1.0
    elif volatility < 0.16:
        return 1.2
    else:
        return 1.4


def predict_price(
    metal_name: str,
    holding_period_months: int = 6,
    investment_amount_usd: float = 10000,
) -> Dict:
    metals = get_all_metals()

    if metal_name not in metals:
        return {"error": f"Metal '{metal_name}' not found"}

    metal_data = metals[metal_name]
    holding_period_years = holding_period_months / 12

    current_price = metal_data["current_price"]
    annual_appreciation = metal_data["annual_appreciation"]

    multiplier = _get_cycle_multiplier(metal_data["annual_volatility"])
    adjusted_appreciation = annual_appreciation * multiplier

    forecasted_price = current_price * ((1 + adjusted_appreciation) ** holding_period_years)

    units = investment_amount_usd / current_price
    gain = (units * forecasted_price) - investment_amount_usd
    roi_percent = (gain / investment_amount_usd) * 100

    if holding_period_months < 12:
        period_display = f"{holding_period_months} months"
    else:
        years = holding_period_months // 12
        months = holding_period_months % 12
        period_display = f"{years}y {months}m" if months > 0 else f"{years} year{'s' if years > 1 else ''}"

    return {
        "metal": metal_name,
        "category": metal_data["category"],
        "symbol": metal_data["symbol"],
        "unit": metal_data["unit"],
        "description": metal_data["description"],
        "current_price_usd": float(round(current_price, 2)),
        "investment_amount_usd": float(round(investment_amount_usd, 2)),
        "units_purchased": float(round(units, 4)),
        "holding_period_display": period_display,
        "forecasted_price_usd": float(round(forecasted_price, 2)),
        "price_change_percent": float(round(((forecasted_price - current_price) / current_price) * 100, 2)),
        "total_gain_usd": float(round(gain, 2)),
        "roi_percent": float(round(roi_percent, 2)),
        "risk_level": "High" if metal_data["annual_volatility"] > 0.16 else "Medium" if metal_data["annual_volatility"] > 0.12 else "Low",
    }


def get_market_stats():
    all_metals = get_all_metals()
    stats = {}
    for category, metals in METALS_DATA.items():
        stats[category] = {}
        for metal_name in metals:
            data = all_metals[metal_name]
            stats[category][metal_name] = {
                "current_price": data["current_price"],
                "unit": data["unit"],
                "volatility": data["annual_volatility"],
                "appreciation": data["annual_appreciation"],
                "price_is_live": data["price_is_live"],
            }
    return stats
