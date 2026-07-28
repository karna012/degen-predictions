#!/usr/bin/env bash
# Installs/updates the automation systemd timers on the live server:
#   - degen-automation:    daily fetch/retrain/predict/track cycle, 05:00 UTC
#   - degen-live-tracker:  live match poll, every 5 minutes
#   - degen-reboot:        instance reboot, 04:00 UTC (ahead of automation)
#
# Run from the repo root, in Git Bash:
#   deploy/setup_automation.sh
set -euo pipefail

HOST="ubuntu@52.201.204.110"
KEY="deploy/degen-predictions-key.pem"
REMOTE_DIR="Degen_predictions"
BUNDLE="/tmp/automation_bundle.tar.gz"

echo "==> Packing automation/ + systemd units"
tar --exclude='__pycache__' --exclude='*.pyc' \
    -czf "$BUNDLE" automation deploy/systemd

echo "==> Uploading"
scp -i "$KEY" "$BUNDLE" "$HOST:~/automation_bundle.tar.gz"

echo "==> Installing on server"
ssh -i "$KEY" "$HOST" "
    cd $REMOTE_DIR &&
    tar xzf ~/automation_bundle.tar.gz &&
    rm ~/automation_bundle.tar.gz &&
    mkdir -p logs performance data/live &&
    sudo cp deploy/systemd/degen-automation.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-automation.timer /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-live-tracker.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-live-tracker.timer /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-reboot.service /etc/systemd/system/ &&
    sudo cp deploy/systemd/degen-reboot.timer /etc/systemd/system/ &&
    sudo systemctl daemon-reload &&
    sudo systemctl enable --now degen-automation.timer &&
    sudo systemctl enable --now degen-live-tracker.timer &&
    sudo systemctl enable --now degen-reboot.timer &&
    echo '--- Installed timers ---' &&
    systemctl list-timers 'degen-*' --no-pager
"

rm -f "$BUNDLE"
echo "==> Done."
