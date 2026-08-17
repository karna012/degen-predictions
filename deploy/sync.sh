#!/usr/bin/env bash
# Pushes local changes (webapp/ + scripts/ by default) up to the live EC2
# server and restarts the app. Run this from the repo root, in Git Bash:
#
#   deploy/sync.sh
#
# Optional: pass extra dirs to sync too, e.g.:
#   deploy/sync.sh model output_tables data
set -euo pipefail

HOST="ubuntu@52.201.204.110"
KEY="deploy/degen-predictions-key.pem"
REMOTE_DIR="Degen_predictions"
# Pack in the repo (not /tmp): Git Bash on Windows has flaky large-file
# writes under /tmp that produced truncated gzip uploads (Unexpected EOF).
BUNDLE="$(cd "$(dirname "$0")/.." && pwd)/sync_bundle.tar.gz"

DIRS_TO_SYNC=(webapp scripts)
if [ "$#" -gt 0 ]; then
    DIRS_TO_SYNC+=("$@")
fi

echo "==> Packing: ${DIRS_TO_SYNC[*]}"
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' \
    --exclude='webapp/users.db' --exclude='webapp/analytics.db' --exclude='webapp/feedback.db' \
    --exclude='webapp/combo_results.db' \
    --exclude='webapp/degen_test_analytics.db' --exclude='webapp/degen_test_feedback.db' \
    --exclude='webapp/sitemap_cache.xml' --exclude='webapp/results_cache.html' \
    --exclude='webapp/mlb_results_cache.html' \
    --exclude='webapp/combo_full.pkl' --exclude='webapp/combo_fast.pkl' \
    --exclude='webapp/.combo_*.pkl' --exclude='webapp/.combo_*.lock' \
    --exclude='webapp/tests/__pycache__' \
    --exclude='data/feedback' \
    --exclude='data/feedback/*' \
    -czf "$BUNDLE" "${DIRS_TO_SYNC[@]}"

echo "==> Uploading"
scp -i "$KEY" "$BUNDLE" "$HOST:~/sync_bundle.tar.gz"

echo "==> Extracting + restarting on server"
ssh -i "$KEY" "$HOST" "
    cd $REMOTE_DIR &&
    tar xzf ~/sync_bundle.tar.gz &&
    rm ~/sync_bundle.tar.gz &&
    bash deploy/ensure_webapp_writable.sh &&
    chmod o+rX -R webapp/static &&
    sudo systemctl restart degen-predictions &&
    sleep 1 &&
    sudo systemctl is-active degen-predictions &&
    curl -fsS -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:8000/
"

rm -f "$BUNDLE"
echo "==> Done. Live at https://degenpredictions.com (or the instance's IP if DNS/HTTPS aren't set up yet)."
