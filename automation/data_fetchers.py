"""
Data fetchers for all prediction categories.
Pulls latest data from APIs and prepares for model training.

NOTE: These fetchers are scaffolding -- logging and structure are in place,
but the actual API integration per category still needs to be wired up to
real endpoints/keys before this produces real data.
"""
import os
import requests
import pandas as pd
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"


class SportsDataFetcher:
    def fetch_football_fixtures(self):
        logger.info("Fetching football fixtures...")
        return pd.DataFrame()

    def fetch_mlb_fixtures(self):
        logger.info("Fetching MLB fixtures...")
        return pd.DataFrame()

    def fetch_results(self):
        logger.info("Fetching sports results...")
        return pd.DataFrame()

    def run(self):
        self.fetch_football_fixtures()
        self.fetch_mlb_fixtures()
        self.fetch_results()


class CryptoDataFetcher:
    def __init__(self):
        self.coingecko = "https://api.coingecko.com/api/v3"

    def fetch_crypto_prices(self):
        try:
            logger.info("Fetching crypto prices from CoinGecko...")
            url = f"{self.coingecko}/simple/price?ids=bitcoin,ethereum,cardano,solana,ripple&vs_currencies=usd&include_24hr_change=true"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.exception("Error fetching crypto prices")
            return {}

    def fetch_crypto_market_cap(self):
        try:
            logger.info("Fetching crypto market cap data...")
            url = f"{self.coingecko}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&sparkline=false"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return pd.DataFrame(response.json())
        except Exception:
            logger.exception("Error fetching market cap")
            return pd.DataFrame()

    def run(self):
        self.fetch_crypto_prices()
        self.fetch_crypto_market_cap()


class StocksDataFetcher:
    def __init__(self):
        self.alpha_vantage_key = os.getenv('ALPHA_VANTAGE_KEY')

    def fetch_us_stocks(self):
        logger.info("Fetching US stock data...")
        return pd.DataFrame()

    def fetch_india_stocks(self):
        logger.info("Fetching India stock data...")
        return pd.DataFrame()

    def run(self):
        self.fetch_us_stocks()
        self.fetch_india_stocks()


class RealEstateDataFetcher:
    """
    Fetches real, current Dubai property prices for dubai_predict.py's ROI
    calculator from the "UAE Real Estate" API on RapidAPI (host below),
    which surfaces actual DLD-registered sale/rent transactions via Bayut --
    not asking prices, not synthetic data. Writes data/dubai/live_prices.json,
    read by dubai_predict.py at request time with a fallback to its static
    defaults if this file is missing or this fetch fails (so a bad API day
    degrades to the old estimate rather than crashing the calculator).

    Free tier is capped at 750 requests/month. One daily run costs 21 calls
    (7 areas x [sale-recent + sale-12mo-ago + rent-recent], page 1 only,
    ~20 transactions per call) = ~630/month, leaving ~120/month headroom for
    manual re-runs. Do not add more areas, pages, or calls without
    re-checking that budget.

    base_appreciation_annual in dubai_predict.DUBAI_AREAS is now a REAL
    trailing 12-month rate computed from actual sale transactions (see
    fetch_appreciation_rates), not hand-set. infrastructure_factor /
    metro_proximity / development_phase stay hand-maintained forward-looking
    adjustments on top of that real base rate -- they represent specific
    catalysts (a metro extension opening, a master plan completing) that a
    trailing 12-month price trend can't fully capture since those catalysts
    haven't happened yet. That's a deliberate modeling layer, not fake data;
    it's documented here so the distinction stays visible.
    """

    RAPIDAPI_HOST = "uae-real-estate3.p.rapidapi.com"

    # Real Bayut neighbourhood externalIDs, resolved via /autocomplete --
    # keys match webapp/dubai_predict.DUBAI_AREAS exactly.
    AREA_LOCATION_IDS = {
        "downtown": "6901",
        "marina": "5003",
        "business_bay": "5093",
        "jumeirah": "5687",
        "al_barsha": "5661",
        "creek_harbour": "8617",
        "south_Dubai": "5663",
    }

    SQFT_PER_SQM = 10.7639

    def __init__(self):
        self.api_key = os.getenv("RAPIDAPI_KEY")
        self.output_path = DATA_DIR / "dubai" / "live_prices.json"

    def _fetch_transactions(self, location_id, purpose, sort_by="date_desc", time_period="3m"):
        response = requests.get(
            f"https://{self.RAPIDAPI_HOST}/transactions",
            params={
                "location_ids": location_id,
                "purpose": purpose,
                "category_ids": "residential",
                "sort_by": sort_by,
                "page": 1,
                "time_period": time_period,
            },
            headers={
                "x-rapidapi-host": self.RAPIDAPI_HOST,
                "x-rapidapi-key": self.api_key,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("hits", [])

    def _avg_price_per_sqft(self, hits):
        per_sqft = [
            float(h["transaction_per_sqm_amount"]) / self.SQFT_PER_SQM
            for h in hits
            if h.get("transaction_per_sqm_amount") and float(h["transaction_per_sqm_amount"]) > 0
        ]
        return (sum(per_sqft) / len(per_sqft), len(per_sqft)) if per_sqft else (None, 0)

    def fetch_property_prices(self):
        """
        Real avg AED/sqft from the most recent registered sales within a
        trailing 12-month window, per area. Stashes the raw hits so
        fetch_appreciation_rates can reuse this same call instead of
        re-fetching (keeps the daily API budget at 21 calls, not 28).
        """
        logger.info("Fetching Dubai property sale prices (real transactions)...")
        results = {}
        self._recent_sale_hits = {}
        for area_key, location_id in self.AREA_LOCATION_IDS.items():
            try:
                hits = self._fetch_transactions(location_id, "for-sale", sort_by="date_desc", time_period="12m")
                self._recent_sale_hits[area_key] = hits
                avg, n = self._avg_price_per_sqft(hits)
                if avg:
                    results[area_key] = {"avg_price_per_sqft": round(avg, 2), "sale_sample_count": n}
                    logger.info(f"  {area_key}: AED {results[area_key]['avg_price_per_sqft']}/sqft ({n} sales)")
                else:
                    logger.warning(f"  {area_key}: no valid sale transactions returned, keeping prior value")
            except Exception:
                logger.exception(f"  {area_key}: sale price fetch failed, keeping prior value")
        return results

    def fetch_appreciation_rates(self):
        """
        Real trailing 12-month capital appreciation per area: avg AED/sqft
        in the most recent sale transactions within a 12-month window
        (reused from fetch_property_prices) vs avg AED/sqft in the OLDEST
        sale transactions in that same window (~12 months back). Only 20
        transactions per leg (free-tier page size) -- a real but noisy
        sample, so the raw rate is clipped to a sane band to guard against
        a single outlier-heavy day producing an absurd swing.
        """
        logger.info("Fetching Dubai trailing 12-month appreciation (real transactions)...")
        results = {}
        for area_key, location_id in self.AREA_LOCATION_IDS.items():
            try:
                recent_hits = getattr(self, "_recent_sale_hits", {}).get(area_key)
                if recent_hits is None:
                    recent_hits = self._fetch_transactions(location_id, "for-sale", sort_by="date_desc", time_period="12m")
                recent_avg, recent_n = self._avg_price_per_sqft(recent_hits)

                old_hits = self._fetch_transactions(location_id, "for-sale", sort_by="date_asc", time_period="12m")
                old_avg, old_n = self._avg_price_per_sqft(old_hits)

                if recent_avg and old_avg and recent_n >= 5 and old_n >= 5:
                    raw_rate = (recent_avg / old_avg) - 1
                    clipped_rate = max(-0.15, min(0.25, raw_rate))
                    results[area_key] = {
                        "trailing_12m_appreciation": round(clipped_rate, 4),
                        "appreciation_sample_count": recent_n + old_n,
                    }
                    note = " (clipped from raw)" if abs(raw_rate - clipped_rate) > 0.001 else ""
                    logger.info(f"  {area_key}: {clipped_rate:.1%} trailing 12mo{note}")
                else:
                    logger.warning(f"  {area_key}: insufficient sample for appreciation calc, keeping prior value")
            except Exception:
                logger.exception(f"  {area_key}: appreciation fetch failed, keeping prior value")
        return results

    def fetch_rental_data(self):
        """Real avg AED/sqft/year from the last 3 months of registered rental contracts, per area."""
        logger.info("Fetching Dubai rental prices (real transactions)...")
        results = {}
        for area_key, location_id in self.AREA_LOCATION_IDS.items():
            try:
                hits = self._fetch_transactions(location_id, "for-rent")
                per_sqft_annual = [
                    (float(h["contract_monthly_amount"]) * 12) / (float(h["builtup_area_sqm"]) * self.SQFT_PER_SQM)
                    for h in hits
                    if h.get("contract_monthly_amount") and h.get("builtup_area_sqm")
                    and float(h["contract_monthly_amount"]) > 0 and float(h["builtup_area_sqm"]) > 0
                ]
                if per_sqft_annual:
                    results[area_key] = {
                        "avg_rent_per_sqft_annual": round(sum(per_sqft_annual) / len(per_sqft_annual), 2),
                        "rental_sample_count": len(per_sqft_annual),
                    }
                    logger.info(f"  {area_key}: AED {results[area_key]['avg_rent_per_sqft_annual']}/sqft/yr ({len(per_sqft_annual)} rentals)")
                else:
                    logger.warning(f"  {area_key}: no valid rental transactions returned, keeping prior value")
            except Exception:
                logger.exception(f"  {area_key}: rental price fetch failed, keeping prior value")
        return results

    def fetch_market_trends(self):
        """
        No live source for this: infrastructure/development-phase factors
        (metro proximity, development phase, appreciation forecast) are
        hand-maintained modeling assumptions in dubai_predict.py, not
        something this transaction API provides. Left as a documented
        no-op rather than fabricating a feed that doesn't exist.
        """
        return {}

    def run(self):
        import json
        from datetime import datetime

        if not self.api_key:
            logger.warning("RAPIDAPI_KEY not set -- skipping Dubai live price refresh, leaving live_prices.json untouched")
            return

        sale_data = self.fetch_property_prices()
        appreciation_data = self.fetch_appreciation_rates()
        rental_data = self.fetch_rental_data()
        self.fetch_market_trends()

        areas = {}
        for area_key in self.AREA_LOCATION_IDS:
            entry = {**sale_data.get(area_key, {}), **appreciation_data.get(area_key, {}), **rental_data.get(area_key, {})}
            if entry:
                areas[area_key] = entry

        if not areas:
            logger.warning("No Dubai price data fetched this run; leaving live_prices.json untouched")
            return

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_updated": datetime.now().isoformat(), "areas": areas}
        with open(self.output_path, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"Saved live Dubai prices for {len(areas)}/{len(self.AREA_LOCATION_IDS)} areas to {self.output_path}")


class MetalsDataFetcher:
    """
    Fetches real live spot prices for metals_predict.py's METALS_DATA,
    preferring TradingView's own public scanner endpoint (the exact same
    backend the /metals page's embedded TradingView widget reads from) so
    the calculator's "current price" matches what the widget displays,
    rather than a similar-but-different number from a second data vendor.
    Falls back to Yahoo Finance for Aluminum, which TradingView's scanner
    won't resolve for free. Writes data/metals/current_prices.json,
    overlaid onto METALS_DATA at runtime.

    Not every metal in METALS_DATA has a reliable free live source, and not
    every metal that HAS one resolves through the same TradingView backend
    the widget uses -- the widget's own JS symbol resolver and the public
    scanner API cover different symbol sets:
    - Copper/Zinc: the widget displays these via Pepperstone CFD symbols
      (PEPPERSTONE:COPPER, PEPPERSTONE:ZINC -- COMEX:HG1! and CAPITALCOM:ZINC
      both gave "invalid symbol" in the actual widget despite COMEX:HG1!
      resolving fine through the scanner API). The scanner endpoint can
      still resolve COMEX:HG1! for Copper's actual price calculation, so
      that's what's used here -- Zinc has no scanner-resolvable equivalent,
      so it stays a static benchmark. Net effect: Copper's forecast price
      and the widget's displayed copper chart won't be pixel-identical,
      because TradingView itself resolves that symbol differently between
      its own subsystems, not because of anything on this end.
    - Aluminum: the widget shows CAPITALCOM:ALUMINUM, which TradingView's
      public scanner endpoint won't resolve without a paid/authenticated
      session (tried the cfd/america/forex/global scanner regions -- all
      empty) -- falls back to Yahoo Finance, a real live quote, just from
      a different venue than the widget.
    - Nickel: no working free retail ticker resolvable server-side (LME
      contracts aren't retail-quoted the way COMEX/CME ones are).
    - Lithium: no standardized exchange-traded spot/futures price at all.
    - Mercury: not in METALS_DATA at all -- no commodity/futures/CFD price
      exists for it on TradingView (searched directly; every result was an
      unrelated company named "Mercury", not the metal), so there is no
      real data source to add it against.
    These stay as periodically hand-updated benchmarks in metals_predict.py.
    """

    # TradingView scanner symbol -> METALS_DATA key. Confirmed resolvable
    # via scanner.tradingview.com/global/scan (server-side, no auth needed).
    TRADINGVIEW_SYMBOLS = {
        "TVC:GOLD": ("Gold", 1.0),
        "TVC:SILVER": ("Silver", 1.0),
        "TVC:PLATINUM": ("Platinum", 1.0),
        "TVC:PALLADIUM": ("Palladium", 1.0),
        "COMEX:HG1!": ("Copper", 2204.62),  # quoted USD/lb -- convert to USD/metric ton
        "COMEX:HRC1!": ("Steel", 1.0),      # USD/short ton (close enough to metric ton for this model)
    }

    # Yahoo Finance fallback for metals TradingView's free scanner can't resolve.
    YAHOO_TICKERS = {
        "ALI=F": ("Aluminum", 1.0),   # already USD/metric ton
    }

    def __init__(self):
        self.output_path = DATA_DIR / "metals" / "current_prices.json"

    def _fetch_tradingview_quotes(self):
        response = requests.post(
            "https://scanner.tradingview.com/global/scan",
            json={"symbols": {"tickers": list(self.TRADINGVIEW_SYMBOLS.keys())}, "columns": ["close"]},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        return {row["s"]: row["d"][0] for row in rows}

    def _fetch_yahoo_quote(self, ticker):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        response = requests.get(
            url, params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        response.raise_for_status()
        return response.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]

    def fetch_metals_prices(self):
        import json
        from datetime import datetime

        prices = {}

        try:
            tv_quotes = self._fetch_tradingview_quotes()
            for symbol, (metal_name, factor) in self.TRADINGVIEW_SYMBOLS.items():
                if symbol in tv_quotes:
                    prices[metal_name] = round(tv_quotes[symbol] * factor, 2)
                    logger.info(f"{metal_name}: {prices[metal_name]} (via TradingView {symbol}, matches widget)")
                else:
                    logger.warning(f"TradingView scanner didn't return {symbol} -- keeping prior value")
        except Exception:
            logger.exception("Failed to fetch TradingView scanner quotes -- keeping prior values for those metals")

        for ticker, (metal_name, factor) in self.YAHOO_TICKERS.items():
            try:
                quote = self._fetch_yahoo_quote(ticker)
                prices[metal_name] = round(quote * factor, 2)
                logger.info(f"{metal_name}: {prices[metal_name]} (via Yahoo {ticker}, NOT the same source as the widget)")
            except Exception:
                logger.exception(f"Failed to fetch {metal_name} ({ticker}) -- keeping prior value")

        if not prices:
            logger.warning("No metal prices fetched this run; leaving current_prices.json untouched")
            return {}

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_updated": datetime.now().isoformat(), "prices": prices}
        with open(self.output_path, "w") as f:
            json.dump(payload, f, indent=2)

        return prices

    def run(self):
        self.fetch_metals_prices()


FETCHERS = {
    'sports': SportsDataFetcher,
    'crypto': CryptoDataFetcher,
    'stocks': StocksDataFetcher,
    'real_estate': RealEstateDataFetcher,
    'metals': MetalsDataFetcher,
}


def fetch_category(category):
    """Run the fetcher for a single category."""
    fetcher_cls = FETCHERS.get(category)
    if not fetcher_cls:
        raise ValueError(f"Unknown category: {category}")
    fetcher_cls().run()


def fetch_all():
    for category in FETCHERS:
        fetch_category(category)


if __name__ == '__main__':
    fetch_all()
