#!/usr/bin/env bash
# Daily refresh of the /dubai ROI calculator's sale + rental prices from
# real DLD-registered transactions (automation.data_fetchers.RealEstateDataFetcher).
# Separate from refresh_daily_data.sh because this one calls a rate-limited
# third-party API (RapidAPI free tier: 750 req/month) -- it must stay on a
# daily cadence, not the 3-hourly one MLB/NPB uses, or it'll exhaust the quota.
set -euo pipefail
cd "$(dirname "$0")/.."
source venv/bin/activate

echo "==> Refreshing Dubai real estate prices (real transactions)"
python -c "from automation.data_fetchers import fetch_category; fetch_category('real_estate')"

echo "==> Done: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
