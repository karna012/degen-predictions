"""
Collect Dubai real estate data from Bayut.com API.

Strategy:
1. Collect current/recent listings for SALES (price trends)
2. Collect current/recent listings for RENTALS (yield data)
3. Track same properties over time to measure appreciation
4. Build 12-month rolling history by collecting weekly

Bayut API is public, no auth required.
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict
import requests
import pandas as pd
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "dubai")
os.makedirs(DATA_DIR, exist_ok=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

BAYUT_API = "https://api.bayut.com/api/v1"
HEADERS = {
    "User-Agent": "DegenPredictions/1.0 (Real estate research)",
}

# Dubai area IDs (from Bayut API)
DUBAI_AREAS = {
    "downtown": {"id": 6300, "name": "Downtown Dubai"},
    "marina": {"id": 6302, "name": "Dubai Marina"},
    "jumeirah": {"id": 6303, "name": "Jumeirah"},
    "business_bay": {"id": 6304, "name": "Business Bay"},
    "jbr": {"id": 6306, "name": "JBR"},
    "dfc": {"id": 6305, "name": "DIFC"},
    "al_barsha": {"id": 6307, "name": "Al Barsha"},
    "Arabian_ranches": {"id": 6309, "name": "Arabian Ranches"},
    "palm_jumeirah": {"id": 6311, "name": "Palm Jumeirah"},
}

# ============================================================================
# 1. COLLECT SALES LISTINGS (For Price/Appreciation)
# ============================================================================

def collect_sales_listings(max_pages: int = 10) -> pd.DataFrame:
    """
    Collect property SALES listings from Bayut.

    Each listing has:
    - Price (current asking price)
    - Posted date (when listed)
    - Property specs (bedrooms, sqft, type)
    - Location (area, community)
    """
    logger.info("Collecting SALES listings from Bayut...")

    all_listings = []

    for area_key, area_info in DUBAI_AREAS.items():
        area_id = area_info["id"]
        area_name = area_info["name"]
        logger.info(f"  Scraping {area_name}...")

        try:
            for page in range(1, max_pages + 1):
                url = f"{BAYUT_API}/search"
                params = {
                    "locationExact": area_id,
                    "transactionType": "buy",
                    "pageNumber": page,
                    "pageSize": 50,
                }

                resp = requests.get(url, headers=HEADERS, params=params, timeout=10)

                if resp.status_code != 200:
                    logger.warning(f"    Page {page}: API returned {resp.status_code}")
                    break

                data = resp.json()
                hits = data.get("hits", 0)

                if hits == 0:
                    logger.info(f"    Page {page}: No more listings")
                    break

                listings = data.get("hits", [])

                for listing in listings:
                    try:
                        all_listings.append({
                            "data_source": "bayut",
                            "transaction_type": "sale",
                            "collection_date": datetime.utcnow().isoformat(),
                            "area": area_name,
                            "area_id": area_id,

                            # Price data
                            "price_aed": listing.get("price"),
                            "listed_date": listing.get("postedDate"),

                            # Property specs
                            "property_type": listing.get("propertyType"),
                            "bedrooms": listing.get("rooms"),
                            "bathrooms": listing.get("baths"),
                            "sqft": listing.get("area"),

                            # Location
                            "community": listing.get("community", {}).get("name") if listing.get("community") else None,

                            # Meta
                            "listing_id": listing.get("id"),
                            "agency": listing.get("agency", {}).get("name") if listing.get("agency") else None,
                        })
                    except Exception as e:
                        logger.debug(f"    Error processing listing: {e}")

                logger.info(f"    Page {page}: Collected {len(listings)} listings")

                # Rate limiting
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"  Error scraping {area_name}: {e}")

    df = pd.DataFrame(all_listings)
    logger.info(f"✓ Collected {len(df)} SALES listings")

    return df

# ============================================================================
# 2. COLLECT RENTAL LISTINGS (For Yield)
# ============================================================================

def collect_rental_listings(max_pages: int = 10) -> pd.DataFrame:
    """
    Collect property RENTAL listings from Bayut.

    Each listing has:
    - Monthly rent (rental price)
    - Posted date
    - Property specs (bedrooms, sqft, type)
    - Location
    """
    logger.info("Collecting RENTAL listings from Bayut...")

    all_listings = []

    for area_key, area_info in DUBAI_AREAS.items():
        area_id = area_info["id"]
        area_name = area_info["name"]
        logger.info(f"  Scraping {area_name}...")

        try:
            for page in range(1, max_pages + 1):
                url = f"{BAYUT_API}/search"
                params = {
                    "locationExact": area_id,
                    "transactionType": "rent",
                    "pageNumber": page,
                    "pageSize": 50,
                }

                resp = requests.get(url, headers=HEADERS, params=params, timeout=10)

                if resp.status_code != 200:
                    logger.warning(f"    Page {page}: API returned {resp.status_code}")
                    break

                data = resp.json()
                hits = data.get("hits", 0)

                if hits == 0:
                    logger.info(f"    Page {page}: No more listings")
                    break

                listings = data.get("hits", [])

                for listing in listings:
                    try:
                        all_listings.append({
                            "data_source": "bayut",
                            "transaction_type": "rent",
                            "collection_date": datetime.utcnow().isoformat(),
                            "area": area_name,
                            "area_id": area_id,

                            # Rental price
                            "monthly_rent_aed": listing.get("price"),
                            "listed_date": listing.get("postedDate"),

                            # Property specs
                            "property_type": listing.get("propertyType"),
                            "bedrooms": listing.get("rooms"),
                            "bathrooms": listing.get("baths"),
                            "sqft": listing.get("area"),

                            # Location
                            "community": listing.get("community", {}).get("name") if listing.get("community") else None,

                            # Meta
                            "listing_id": listing.get("id"),
                            "agency": listing.get("agency", {}).get("name") if listing.get("agency") else None,
                        })
                    except Exception as e:
                        logger.debug(f"    Error processing listing: {e}")

                logger.info(f"    Page {page}: Collected {len(listings)} listings")

                # Rate limiting
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"  Error scraping {area_name}: {e}")

    df = pd.DataFrame(all_listings)
    logger.info(f"✓ Collected {len(df)} RENTAL listings")

    return df

# ============================================================================
# 3. MERGE SALES + RENTALS TO COMPUTE YIELD
# ============================================================================

def merge_sales_and_rentals(sales_df: pd.DataFrame, rentals_df: pd.DataFrame) -> pd.DataFrame:
    """
    Match sales and rental listings by area/type/bedrooms to compute yield.

    For example:
    - 2BR apartment in Downtown: Avg sale price 1.5M AED, Avg rent 5,000 AED/month
    - Yield = (5000 * 12) / 1500000 = 4% annual
    """
    logger.info("Computing rental yields...")

    # Group sales by area/type/bedrooms
    sales_grouped = sales_df.groupby(["area", "property_type", "bedrooms"]).agg({
        "price_aed": ["mean", "count"],
        "sqft": "mean",
    }).reset_index()
    sales_grouped.columns = ["area", "property_type", "bedrooms", "avg_sale_price", "sale_count", "avg_sqft"]

    # Group rentals by area/type/bedrooms
    rentals_grouped = rentals_df.groupby(["area", "property_type", "bedrooms"]).agg({
        "monthly_rent_aed": ["mean", "count"],
    }).reset_index()
    rentals_grouped.columns = ["area", "property_type", "bedrooms", "avg_monthly_rent", "rental_count"]

    # Merge
    merged = sales_grouped.merge(
        rentals_grouped,
        on=["area", "property_type", "bedrooms"],
        how="outer"
    )

    # Compute yield
    merged["annual_rent_aed"] = merged["avg_monthly_rent"] * 12
    merged["annual_yield_percent"] = (merged["annual_rent_aed"] / merged["avg_sale_price"]) * 100
    merged["collection_date"] = datetime.utcnow().isoformat()

    logger.info(f"✓ Computed yields for {len(merged)} property segments")

    return merged

# ============================================================================
# 4. SAVE AND TRACK OVER TIME
# ============================================================================

def save_data(sales_df: pd.DataFrame, rentals_df: pd.DataFrame, merged_df: pd.DataFrame):
    """Save all data for training."""

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # Save raw listings
    sales_file = os.path.join(DATA_DIR, f"bayut_sales_{timestamp}.csv")
    sales_df.to_csv(sales_file, index=False)
    logger.info(f"✓ Saved sales to {sales_file}")

    rentals_file = os.path.join(DATA_DIR, f"bayut_rentals_{timestamp}.csv")
    rentals_df.to_csv(rentals_file, index=False)
    logger.info(f"✓ Saved rentals to {rentals_file}")

    # Save computed yields
    yields_file = os.path.join(DATA_DIR, f"bayut_yields_{timestamp}.csv")
    merged_df.to_csv(yields_file, index=False)
    logger.info(f"✓ Saved yields to {yields_file}")

    # Also save a combined file for easy access
    combined_file = os.path.join(DATA_DIR, "bayut_latest_combined.csv")
    merged_df.to_csv(combined_file, index=False)
    logger.info(f"✓ Saved latest combined to {combined_file}")

# ============================================================================
# 5. TRACK HISTORICAL APPRECIATION
# ============================================================================

def track_appreciation_over_time():
    """
    Compare price changes across multiple collections.

    If we have 2+ weeks of data, we can compute price appreciation.
    """
    logger.info("Tracking price appreciation over time...")

    # Find all yield files
    yield_files = sorted([f for f in os.listdir(DATA_DIR) if f.startswith("bayut_yields_")])

    if len(yield_files) < 2:
        logger.warning("Need at least 2 collection dates to compute appreciation. Come back later.")
        return None

    # Load oldest and newest
    oldest_file = os.path.join(DATA_DIR, yield_files[0])
    newest_file = os.path.join(DATA_DIR, yield_files[-1])

    oldest_df = pd.read_csv(oldest_file)
    newest_df = pd.read_csv(newest_file)

    # Merge on area/type/bedrooms to compare prices
    comparison = oldest_df[["area", "property_type", "bedrooms", "avg_sale_price"]].merge(
        newest_df[["area", "property_type", "bedrooms", "avg_sale_price", "collection_date"]],
        on=["area", "property_type", "bedrooms"],
        suffixes=("_old", "_new")
    )

    comparison["price_change_aed"] = comparison["avg_sale_price_new"] - comparison["avg_sale_price_old"]
    comparison["price_change_percent"] = (comparison["price_change_aed"] / comparison["avg_sale_price_old"]) * 100

    logger.info(f"✓ Appreciation tracking over {len(yield_files)} collections:")
    for idx, row in comparison.iterrows():
        if pd.notna(row["price_change_percent"]):
            logger.info(f"  {row['area']} {row['property_type']} {row['bedrooms']}BR: {row['price_change_percent']:.2f}%")

    # Save appreciation data
    appreciation_file = os.path.join(DATA_DIR, "bayut_appreciation_tracking.csv")
    comparison.to_csv(appreciation_file, index=False)
    logger.info(f"✓ Saved appreciation tracking to {appreciation_file}")

    return comparison

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("BAYUT DATA COLLECTION - DUBAI REAL ESTATE")
    logger.info("=" * 70)
    logger.info(f"Data directory: {DATA_DIR}")
    logger.info(f"Collection started: {datetime.utcnow().isoformat()}")
    logger.info("")

    # Step 1: Collect sales
    sales_df = collect_sales_listings(max_pages=5)

    # Step 2: Collect rentals
    logger.info("")
    rentals_df = collect_rental_listings(max_pages=5)

    # Step 3: Compute yields
    logger.info("")
    merged_df = merge_sales_and_rentals(sales_df, rentals_df)

    # Step 4: Save
    logger.info("")
    save_data(sales_df, rentals_df, merged_df)

    # Step 5: Track appreciation (if we have history)
    logger.info("")
    appreciation = track_appreciation_over_time()

    logger.info("")
    logger.info("=" * 70)
    logger.info("✅ COLLECTION COMPLETE")
    logger.info("=" * 70)
    logger.info(f"Sales listings: {len(sales_df)}")
    logger.info(f"Rental listings: {len(rentals_df)}")
    logger.info(f"Computed segments: {len(merged_df)}")
    logger.info("")
    logger.info("Next step: python scripts/train_dubai_models.py")
