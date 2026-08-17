#!/usr/bin/env bash
# Webapp-only sync (avoids packing huge scripts/). Run from repo root:
#   bash deploy/sync_webapp_only.sh
set -euo pipefail

HOST="ubuntu@52.201.204.110"
KEY="deploy/degen-predictions-key.pem"
REMOTE_DIR="Degen_predictions"
BUNDLE="$(cd "$(dirname "$0")/.." && pwd)/sync_bundle_webapp.tar.gz"
cd "$(dirname "$0")/.."

echo "==> Packing webapp only"
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' \
    --exclude='webapp/users.db' --exclude='webapp/analytics.db' --exclude='webapp/feedback.db' \
    --exclude='webapp/combo_results.db' \
    --exclude='webapp/sitemap_cache.xml' --exclude='webapp/results_cache.html' \
    --exclude='webapp/mlb_results_cache.html' \
    --exclude='webapp/combo_full.pkl' --exclude='webapp/combo_fast.pkl' \
    --exclude='webapp/tests/__pycache__' \
    --exclude='webapp/static/space/_src' \
    --exclude='webapp/static/space/_build_sprites.py' \
    -czf "$BUNDLE" webapp
ls -lh "$BUNDLE"

echo "==> Uploading"
# Absolute remote path: Windows OpenSSH scp does not reliably expand ~/
scp -i "$KEY" -o StrictHostKeyChecking=accept-new "$BUNDLE" "${HOST}:/home/ubuntu/sync_bundle.tar.gz"

echo "==> Extracting + restarting on server"
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" "
    cd $REMOTE_DIR &&
    tar xzf /home/ubuntu/sync_bundle.tar.gz &&
    rm /home/ubuntu/sync_bundle.tar.gz &&
    bash deploy/ensure_webapp_writable.sh &&
    chmod o+rX -R webapp/static &&
    sudo systemctl restart degen-predictions &&
    sleep 1 &&
    sudo systemctl is-active degen-predictions &&
    curl -fsS -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:8000/
"

rm -f "$BUNDLE"
echo "==> Done. Live at https://degenpredictions.com"
