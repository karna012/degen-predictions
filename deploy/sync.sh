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
BUNDLE="/tmp/sync_bundle.tar.gz"

DIRS_TO_SYNC=(webapp scripts)
if [ "$#" -gt 0 ]; then
    DIRS_TO_SYNC+=("$@")
fi

echo "==> Packing: ${DIRS_TO_SYNC[*]}"
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' \
    --exclude='webapp/users.db' --exclude='webapp/analytics.db' --exclude='webapp/feedback.db' \
    --exclude='webapp/sitemap_cache.xml' --exclude='webapp/results_cache.html' \
    --exclude='webapp/tests/__pycache__' \
    -czf "$BUNDLE" "${DIRS_TO_SYNC[@]}"

echo "==> Uploading"
scp -i "$KEY" "$BUNDLE" "$HOST:~/sync_bundle.tar.gz"

echo "==> Extracting + restarting on server"
ssh -i "$KEY" "$HOST" "
    cd $REMOTE_DIR &&
    tar xzf ~/sync_bundle.tar.gz &&
    rm ~/sync_bundle.tar.gz &&
    chmod o+rX -R webapp/static &&
    sudo systemctl restart degen-predictions &&
    sleep 1 &&
    sudo systemctl is-active degen-predictions
"

rm -f "$BUNDLE"
echo "==> Done. Live at https://degenpredictions.com (or the instance's IP if DNS/HTTPS aren't set up yet)."
