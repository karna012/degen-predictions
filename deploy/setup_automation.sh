#!/usr/bin/env bash
# Installs/updates the automation systemd timers on the live server:
#   - degen-live-tracker:       live match poll, every 5 minutes
#   - degen-post-event:         live-score settle + results caches, every 5 minutes
#   - degen-daily-distribute:   Top-3 digest + catch-up (03:30/05:00/07:30/10:00 UTC)
#   - degen-reboot:             instance reboot, 04:00 UTC
#
# (The old degen-automation fetch/retrain stub timer was removed -- it was
# scaffolding that never trained real models and was unsafe to run on EC2.)
#
# Run from the repo root, in Git Bash:
#   deploy/setup_automation.sh
set -euo pipefail

HOST="ubuntu@52.201.204.110"
KEY="deploy/degen-predictions-key.pem"
REMOTE_DIR="Degen_predictions"
BUNDLE="/tmp/automation_bundle.tar.gz"

echo "==> Packing automation/ + systemd units + daily distribute"
tar --exclude='__pycache__' --exclude='*.pyc' \
    -czf "$BUNDLE" automation deploy/systemd deploy/daily_distribute.sh

echo "==> Uploading"
scp -i "$KEY" "$BUNDLE" "$HOST:~/automation_bundle.tar.gz"

echo "==> Installing on server"
ssh -i "$KEY" "$HOST" "
    cd $REMOTE_DIR &&
    tar xzf ~/automation_bundle.tar.gz &&
    rm ~/automation_bundle.tar.gz &&
    mkdir -p logs performance data/live data/daily_digest output_tables/daily_drafts &&
    chmod +x deploy/daily_distribute.sh &&
    sudo cp deploy/systemd/degen-live-tracker.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-live-tracker.timer /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-post-event.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-post-event.timer /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-daily-distribute.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-daily-distribute.timer /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-reboot.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-reboot.timer /etc/systemd/system/ &&
    sudo systemctl daemon-reload &&
    # Tear down the removed stub automation timer if it was previously installed
    sudo systemctl disable --now degen-automation.timer 2>/dev/null || true
    sudo rm -f /etc/systemd/system/degen-automation.service /etc/systemd/system/degen-automation.timer
    sudo systemctl daemon-reload &&
    sudo systemctl enable --now degen-live-tracker.timer &&
    sudo systemctl enable --now degen-post-event.timer &&
    sudo systemctl enable --now degen-daily-distribute.timer &&
    sudo systemctl enable --now degen-reboot.timer &&
    echo '--- Installed timers ---' &&
    systemctl list-timers 'degen-*' --no-pager
"

rm -f "$BUNDLE"
echo "==> Done."
