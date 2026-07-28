"""
Metals & Stones price prediction and investment calculator.
"""
import numpy as np
from typing import Dict

METALS_DATA = {
    "precious": {
        "Gold": {
            "symbol": "XAU",
            "unit": "USD/oz",
            "current_price": 2050,
            "annual_volatility": 0.12,
            "annual_appreciation": 0.085,
            "description": "Yellow precious metal",
        },
        "Silver": {
            "symbol": "XAG",
            "unit": "USD/oz",
            "current_price": 24.50,
            "annual_volatility": 0.18,
            "annual_appreciation": 0.095,
            "description": "White precious metal",
        },
        "Platinum": {
            "symbol": "XPT",
            "unit": "USD/oz",
            "current_price": 950,
            "annual_volatility": 0.15,
            "annual_appreciation": 0.065,
            "description": "Rare precious metal",
        },
        "Palladium": {
            "symbol": "XPD",
            "unit": "USD/oz",
            "current_price": 950,
            "annual_volatility": 0.20,
            "annual_appreciation": 0.075,
            "description": "Industrial precious metal",
        },
    },
    "industrial": {
        "Copper": {
            "symbol": "HG",
            "unit": "USD/ton",
            "current_price": 9500,
            "annual_volatility": 0.16,
            "annual_appreciation": 0.075,
            "description": "Red industrial metal",
        },
        "Aluminum": {
            "symbol": "AL",
            "unit": "USD/ton",
            "current_price": 2400,
            "annual_volatility": 0.14,
            "annual_appreciation": 0.055,
            "description": "Lightweight industrial metal",
        },
        "Steel": {
            "symbol": "SI",
            "unit": "USD/ton",
            "current_price": 650,
            "annual_volatility": 0.12,
            "annual_appreciation": 0.045,
            "description": "Iron-based alloy",
        },
        "Zinc": {
            "symbol": "ZN",
            "unit": "USD/ton",
            "current_price": 2750,
            "annual_volatility": 0.15,
            "annual_appreciation": 0.065,
            "description": "Corrosion-resistant metal",
        },
        "Nickel": {
            "symbol": "NI",
            "unit": "USD/ton",
            "current_price": 8200,
            "annual_volatility": 0.18,
            "annual_appreciation": 0.085,
            "description": "Stainless steel component",
        },
        "Lithium": {
            "symbol": "LI",
            "unit": "USD/ton",
            "current_price": 45000,
            "annual_volatility": 0.22,
            "annual_appreciation": 0.15,
            "description": "Battery metal",
        },
    },
    "gemstones": {
        "Diamond": {
            "symbol": "DIA",
            "unit": "USD/carat",
            "current_price": 5500,
            "annual_volatility": 0.08,
            "annual_appreciation": 0.055,
            "description": "Precious gemstone",
        },
        "Ruby": {
            "symbol": "RUB",
            "unit": "USD/carat",
            "current_price": 3000,
            "annual_volatility": 0.10,
            "annual_appreciation": 0.075,
            "description": "Red gemstone",
        },
        "Emerald": {
            "symbol": "EME",
            "unit": "USD/carat",
            "current_price": 2000,
            "annual_volatility": 0.12,
            "annual_appreciation": 0.065,
            "description": "Green gemstone",
        },
        "Sapphire": {
            "symbol": "SAP",
            "unit": "USD/carat",
            "current_price": 2500,
            "annual_volatility": 0.11,
            "annual_appreciation": 0.07,
            "description": "Blue gemstone",
        },
    },
}


def get_categories():
    return list(METALS_DATA.keys())


def get_metals_by_category(category: str):
    return list(METALS_DATA.get(category, {}).keys())


def get_all_metals():
    all_metals = {}
    for category, metals in METALS_DATA.items():
        for metal_name, data in metals.items():
            all_metals[metal_name] = {**data, "category": category}
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
    stats = {}
    for category, metals in METALS_DATA.items():
        stats[category] = {}
        for metal_name, data in metals.items():
            stats[category][metal_name] = {
                "current_price": data["current_price"],
                "unit": data["unit"],
                "volatility": data["annual_volatility"],
                "appreciation": data["annual_appreciation"],
            }
    return stats
