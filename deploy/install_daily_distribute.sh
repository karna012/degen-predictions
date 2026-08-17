#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="ubuntu@52.201.204.110"
KEY="deploy/degen-predictions-key.pem"
BUNDLE="/tmp/daily_dist_bundle.tar.gz"

tar --exclude='__pycache__' -czf "$BUNDLE" \
  deploy/daily_distribute.sh \
  deploy/daily_distribute.ps1 \
  deploy/systemd/degen-daily-distribute.service \
  deploy/systemd/degen-daily-distribute.timer \
  deploy/production.env.example \
  docs/DAILY_DISTRIBUTION.md \
  deploy/setup_automation.sh

scp -i "$KEY" -o StrictHostKeyChecking=accept-new "$BUNDLE" "${HOST}:/home/ubuntu/daily_dist_bundle.tar.gz"

ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/Degen_predictions
tar xzf /home/ubuntu/daily_dist_bundle.tar.gz
rm -f /home/ubuntu/daily_dist_bundle.tar.gz
mkdir -p logs data/daily_digest output_tables/daily_drafts
chmod +x deploy/daily_distribute.sh
sudo cp deploy/systemd/degen-daily-distribute.service /etc/systemd/system/
sudo cp deploy/systemd/degen-daily-distribute.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now degen-daily-distribute.timer
echo "=== timers ==="
systemctl list-timers 'degen-daily-distribute*' --no-pager
echo "=== dry run job ==="
# Prefer dry-run for first enable so we don't fail if pillow/env oddities
cd webapp
../venv/bin/python3 daily_digest.py --dry-run --use-cache --print-share-text || ../venv/bin/python3 daily_digest.py --dry-run --print-share-text
echo "=== files ==="
ls -la ../data/daily_digest/ | head
REMOTE

rm -f "$BUNDLE"
echo "==> Deploy distribute DONE"
