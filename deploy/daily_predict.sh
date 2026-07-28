#!/usr/bin/env bash
# Linux equivalent of scripts/daily_predict.bat -- regenerates upcoming-fixture
# predictions for all 5 leagues. Wired up via cron, see DEPLOY.md step 9:
#   crontab -e
#   0 6 * * * /home/ubuntu/Degen_predictions/deploy/daily_predict.sh >> /home/ubuntu/Degen_predictions/daily_predict.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
source venv/bin/activate
python scripts/predict_pl.py
echo "---- $(date) ----"
