"""
Dubai real estate ROI prediction interface for the webapp.

Provides:
- Property area/type/size selector
- Investment calculator
- ROI predictions (capital appreciation + rental yield)
"""
import os
import sys
import joblib
import pandas as pd
import numpy as np
from typing import Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, "model", "dubai")
DATA_DIR = os.path.join(BASE_DIR, "data", "dubai")

# Predefined Dubai areas with infrastructure-based development model
DUBAI_AREAS = {
    "downtown": {
        "name": "Downtown Dubai",
        "avg_price_per_sqft": 5200,
        "avg_rent_per_sqft_annual": 540,
        "base_appreciation_annual": 0.04,
        "metro_proximity": "adjacent",  # Adjacent to downtown metro
        "development_phase": "mature",  # Established, limited new development
        "infrastructure_factor": 1.0,   # Reference baseline
        "commercial_density": "high",
    },
    "marina": {
        "name": "Dubai Marina",
        "avg_price_per_sqft": 4800,
        "avg_rent_per_sqft_annual": 504,
        "base_appreciation_annual": 0.035,
        "metro_proximity": "nearby",  # 15-min walk to metro
        "development_phase": "established",
        "infrastructure_factor": 0.95,
        "commercial_density": "medium-high",
    },
    "business_bay": {
        "name": "Business Bay",
        "avg_price_per_sqft": 4200,
        "avg_rent_per_sqft_annual": 456,
        "base_appreciation_annual": 0.045,
        "metro_proximity": "adjacent",  # Metro line passes through
        "development_phase": "growth",  # Active commercial development
        "infrastructure_factor": 1.15,
        "commercial_density": "very-high",
    },
    "jumeirah": {
        "name": "Jumeirah",
        "avg_price_per_sqft": 6500,
        "avg_rent_per_sqft_annual": 660,
        "base_appreciation_annual": 0.038,
        "metro_proximity": "within_2km",  # Served by extended metro
        "development_phase": "established",
        "infrastructure_factor": 0.90,  # Exclusive area, limited new development
        "commercial_density": "low",
    },
    "al_barsha": {
        "name": "Al Barsha",
        "avg_price_per_sqft": 3500,
        "avg_rent_per_sqft_annual": 385,
        "base_appreciation_annual": 0.055,
        "metro_proximity": "adjacent",  # Metro Mall station
        "development_phase": "rapid-growth",  # New malls, flyovers, roads
        "infrastructure_factor": 1.35,  # Continuous infrastructure expansion
        "commercial_density": "medium",
    },
    "creek_harbour": {
        "name": "Creek Harbour",
        "avg_price_per_sqft": 3800,
        "avg_rent_per_sqft_annual": 420,
        "base_appreciation_annual": 0.08,
        "metro_proximity": "upcoming",  # Planned metro extension
        "development_phase": "emerging",  # New master-planned community
        "infrastructure_factor": 1.50,  # Heavy infrastructure build-out
        "commercial_density": "medium",
    },
    "south_Dubai": {
        "name": "South Dubai (Jebel Ali)",
        "avg_price_per_sqft": 2800,
        "avg_rent_per_sqft_annual": 320,
        "base_appreciation_annual": 0.065,
        "metro_proximity": "planned",  # Future metro extension
        "development_phase": "emerging",  # Port, new developments, islands
        "infrastructure_factor": 1.45,  # Major infrastructure corridor
        "commercial_density": "medium-high",
    },
}

PROPERTY_TYPES = {
    "apartment": "Apartment",
    "villa": "Villa",
    "townhouse": "Townhouse",
}

BEDROOM_CONFIGS = {
    "studio": {"name": "Studio", "avg_sqft": 600},
    "1br": {"name": "1 Bedroom", "avg_sqft": 900},
    "2br": {"name": "2 Bedrooms", "avg_sqft": 1300},
    "3br": {"name": "3 Bedrooms", "avg_sqft": 1800},
    "4br": {"name": "4 Bedrooms", "avg_sqft": 2500},
    "5br": {"name": "5+ Bedrooms", "avg_sqft": 3500},
}

def _calculate_infrastructure_appreciation_multiplier(area_data: Dict, property_type: str, holding_period_years: float) -> float:
    """
    Calculate appreciation multiplier based on infrastructure development.

    Factors:
    - Metro proximity (adjacent +20%, nearby +10%, planned +15%)
    - Development phase (rapid-growth +40%, emerging +50%, growth +20%)
    - Infrastructure factor (multiplier 0.9-1.5x)
    - Property type (commercial zones and emerging areas boost residential)
    """
    metro_multipliers = {
        "adjacent": 1.20,      # Highest accessibility
        "nearby": 1.10,        # 15-min walk
        "within_2km": 1.05,    # Car access
        "planned": 1.15,       # Future metro coming
        "upcoming": 1.18,      # Planned extension
    }

    development_multipliers = {
        "mature": 0.95,           # Limited new development
        "established": 1.0,       # Baseline growth
        "growth": 1.20,           # Active development
        "rapid-growth": 1.40,     # Heavy infrastructure investment
        "emerging": 1.50,         # New master-planned community
    }

    # Property type adjustment in growth areas
    property_type_bonus = {
        "apartment": 1.0,
        "villa": 0.95,            # Villas appreciate slightly slower in dense commercial areas
        "townhouse": 0.98,
    }

    metro_multi = metro_multipliers.get(area_data.get("metro_proximity", "nearby"), 1.0)
    dev_multi = development_multipliers.get(area_data.get("development_phase", "established"), 1.0)
    infra_factor = area_data.get("infrastructure_factor", 1.0)
    prop_bonus = property_type_bonus.get(property_type, 1.0)

    # Time decay: Infrastructure benefits diminish after 10-15 years as area matures
    time_decay = 1.0
    if holding_period_years > 10:
        time_decay = max(0.8, 1.0 - (holding_period_years - 10) * 0.02)  # Decline 2% per year after year 10

    # Combined multiplier with infrastructure factor
    combined = metro_multi * dev_multi * prop_bonus * infra_factor * time_decay

    return round(combined, 3)


def load_models():
    """Load trained appreciation and rental yield models."""
    appreciation_file = os.path.join(MODEL_DIR, "appreciation_model.joblib")
    rental_file = os.path.join(MODEL_DIR, "rental_yield_model.joblib")

    appreciation_model = None
    rental_model = None

    if os.path.exists(appreciation_file):
        appreciation_model = joblib.load(appreciation_file)
    if os.path.exists(rental_file):
        rental_model = joblib.load(rental_file)

    return appreciation_model, rental_model

def predict_roi(
    area: str,
    property_type: str,
    bedroom_config: str,
    investment_amount_aed: float,
    holding_period_months: int = 6,
) -> Dict:
    """
    Predict ROI on a Dubai property investment.

    Args:
        area: Key from DUBAI_AREAS (e.g., "downtown")
        property_type: Key from PROPERTY_TYPES (e.g., "apartment")
        bedroom_config: Key from BEDROOM_CONFIGS (e.g., "2br")
        investment_amount_aed: Total investment in AED
        holding_period_months: 6 or 12 months

    Returns:
        {
            "investment_aed": 1500000,
            "holding_period_months": 6,
            "area": "Downtown Dubai",
            "property_type": "Apartment",
            "bedrooms": "2 Bedrooms",

            "estimated_property_sqft": 1300,
            "estimated_price_per_sqft": 5200,

            "capital_appreciation_percent": 1.5,  # 3% annual / 2
            "capital_appreciation_aed": 22500,
            "property_value_after_holding": 1522500,

            "estimated_annual_rent_aed": 52560,  # 540 per sqft * 1300 sqft / 12
            "estimated_holding_period_rent": 26280,  # Half-year if 6mo holding

            "total_gain_aed": 48780,  # Capital appreciation + rent
            "total_roi_percent": 3.25,  # (48780 / 1500000) * 100

            "annual_rental_yield": 3.51,  # (52560 / 1500000) * 100
            "data_confidence": "high",
            "market_outlook": "Strong growth expected in Downtown Dubai",
        }
    """
    if area not in DUBAI_AREAS or property_type not in PROPERTY_TYPES or bedroom_config not in BEDROOM_CONFIGS:
        return {"error": "Invalid area, property type, or bedroom config"}

    area_data = DUBAI_AREAS[area]
    bedroom_data = BEDROOM_CONFIGS[bedroom_config]
    holding_period_years = holding_period_months / 12

    # Estimate property square footage and value
    estimated_sqft = bedroom_data["avg_sqft"]
    estimated_price_per_sqft = area_data["avg_price_per_sqft"]
    estimated_property_value = estimated_sqft * estimated_price_per_sqft

    # Normalize investment to property value (if user specifies custom amount)
    sqft_ratio = investment_amount_aed / estimated_property_value
    adjusted_sqft = estimated_sqft * sqft_ratio
    adjusted_property_value = investment_amount_aed

    # Capital appreciation with infrastructure multiplier (compound growth)
    base_appreciation_rate = area_data["base_appreciation_annual"]
    infrastructure_multiplier = _calculate_infrastructure_appreciation_multiplier(
        area_data, property_type, holding_period_years
    )
    # Adjusted appreciation = base rate * infrastructure multiplier (but cap at reasonable limits)
    adjusted_appreciation_rate = min(base_appreciation_rate * infrastructure_multiplier, 0.20)  # Cap at 20% annual

    property_value_after = adjusted_property_value * ((1 + adjusted_appreciation_rate) ** holding_period_years)
    capital_appreciation = property_value_after - adjusted_property_value

    # Rental income
    annual_rent_per_sqft = area_data["avg_rent_per_sqft_annual"]
    annual_rent = adjusted_sqft * annual_rent_per_sqft
    holding_period_rent = annual_rent * holding_period_years

    # Combined ROI
    total_gain = capital_appreciation + holding_period_rent
    total_roi_percent = (total_gain / investment_amount_aed) * 100
    annual_rental_yield = (annual_rent / investment_amount_aed) * 100

    # Infrastructure-based market outlook
    infrastructure_outlooks = {
        "downtown": "Established premium location; limited new development; stable capital appreciation.",
        "marina": "Mature waterfront area; balanced growth; strong rental yields.",
        "business_bay": "Active metro connectivity; commercial development driving growth; rising appreciation.",
        "jumeirah": "Luxury segment; exclusive development; steady appreciation; premium rental yields.",
        "al_barsha": "High infrastructure investment; metro proximity; rapid commercial expansion; strong growth.",
        "creek_harbour": "Emerging master-planned community; upcoming metro extension; significant infrastructure build-out; highest growth potential.",
        "south_dubai": "Planned major infrastructure corridor (Jebel Ali expansion); emerging opportunities; strong long-term appreciation.",
    }

    return {
        "investment_aed": int(investment_amount_aed),
        "holding_period_months": holding_period_months,
        "area": area_data["name"],
        "property_type": PROPERTY_TYPES[property_type],
        "bedrooms": bedroom_data["name"],

        "estimated_property_sqft": int(adjusted_sqft),
        "estimated_price_per_sqft": int(estimated_price_per_sqft),

        "capital_appreciation_percent": round(adjusted_appreciation_rate * holding_period_years * 100, 2),
        "capital_appreciation_aed": int(capital_appreciation),
        "property_value_after_holding": int(property_value_after),
        "adjusted_annual_appreciation_rate": round(adjusted_appreciation_rate * 100, 2),

        "estimated_annual_rent_aed": int(annual_rent),
        "estimated_holding_period_rent_aed": int(holding_period_rent),

        "total_gain_aed": int(total_gain),
        "total_roi_percent": round(total_roi_percent, 2),

        "annual_rental_yield_percent": round(annual_rental_yield, 2),

        # Infrastructure factors
        "infrastructure_factors": {
            "metro_proximity": area_data.get("metro_proximity", "unknown"),
            "development_phase": area_data.get("development_phase", "unknown"),
            "infrastructure_multiplier": infrastructure_multiplier,
            "commercial_density": area_data.get("commercial_density", "unknown"),
        },

        "data_confidence": "high",
        "market_outlook": infrastructure_outlooks.get(area, "Market data available."),
        "disclaimer": "Predictions based on linear infrastructure development patterns. Actual returns depend on market conditions, property maintenance, rental demand, and economic changes.",
    }

def get_areas_list():
    """Return list of Dubai areas for UI dropdown."""
    return {k: v["name"] for k, v in DUBAI_AREAS.items()}

def get_property_types_list():
    """Return list of property types for UI dropdown."""
    return PROPERTY_TYPES

def get_bedroom_configs_list():
    """Return list of bedroom configurations for UI dropdown."""
    return {k: v["name"] for k, v in BEDROOM_CONFIGS.items()}
