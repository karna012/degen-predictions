#!/usr/bin/env bash
# Run this ONCE on a fresh Ubuntu 22.04/24.04 EC2 instance to stand up
# Degen Predictions behind nginx + gunicorn. See deploy/DEPLOY.md for the
# full step-by-step runbook this script is part of.
#
# Usage (as the ubuntu user, after cloning the repo to ~/Degen_predictions):
#   cd ~/Degen_predictions
#   chmod +x deploy/setup_ec2.sh
#   ./deploy/setup_ec2.sh

set -euo pipefail

REPO_DIR="$HOME/Degen_predictions"
cd "$REPO_DIR"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx fail2ban

echo "==> Creating virtualenv"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r deploy/requirements-prod.txt

echo "==> Setting up environment file"
if [ ! -f deploy/production.env ]; then
    cp deploy/production.env.example deploy/production.env
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/replace-this-with-a-real-random-64-char-hex-string/$SECRET/" deploy/production.env
    echo "    Generated a random FLASK_SECRET_KEY in deploy/production.env"
else
    echo "    deploy/production.env already exists, leaving it as-is"
fi

echo "==> Installing systemd service"
sudo cp deploy/degen-predictions.service /etc/systemd/system/degen-predictions.service
sudo systemctl daemon-reload
sudo systemctl enable degen-predictions
sudo systemctl restart degen-predictions

echo "==> Configuring nginx (rate limits + site)"
sudo cp deploy/nginx-limits.conf /etc/nginx/conf.d/degen-limits.conf
sudo cp deploy/nginx.conf /etc/nginx/sites-available/degen-predictions
sudo ln -sf /etc/nginx/sites-available/degen-predictions /etc/nginx/sites-enabled/degen-predictions
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "==> Configuring fail2ban"
sudo cp deploy/jail.local /etc/fail2ban/jail.local
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

echo ""
echo "==> Done. Next steps:"
echo "    1. Edit /etc/nginx/sites-available/degen-predictions and replace YOUR_DOMAIN"
echo "       with your real domain, then: sudo nginx -t && sudo systemctl reload nginx"
echo "    2. Point your domain's DNS A record at this instance's Elastic IP (Route 53)"
echo "    3. Once DNS resolves, run: sudo certbot --nginx -d YOUR_DOMAIN -d www.YOUR_DOMAIN"
echo "    4. Check status any time with: sudo systemctl status degen-predictions"
