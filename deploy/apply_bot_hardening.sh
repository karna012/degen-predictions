#!/usr/bin/env bash
# Apply bot-hardening on a live EC2 box without wiping certbot HTTPS config.
# Run on the server from repo root:
#   bash deploy/apply_bot_hardening.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="/etc/nginx/sites-available/degen-predictions"
LIMITS="/etc/nginx/conf.d/degen-limits.conf"

echo "==> fail2ban"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban
sudo cp "$ROOT/deploy/jail.local" /etc/fail2ban/jail.local
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

echo "==> nginx rate-limit zones"
sudo cp "$ROOT/deploy/nginx-limits.conf" "$LIMITS"

echo "==> patch live site config (non-destructive)"
sudo python3 - <<'PY'
from pathlib import Path
path = Path("/etc/nginx/sites-available/degen-predictions")
if not path.is_file():
    # fallback common names
    for cand in Path("/etc/nginx/sites-available").iterdir():
        if cand.is_file() and "degen" in cand.name.lower():
            path = cand
            break
text = path.read_text(encoding="utf-8")
orig = text

if "client_max_body_size" not in text:
    text = text.replace("server {", "server {\n    client_max_body_size 8m;", 1)

def ensure_limit(block_start: str, zone: str, burst: str) -> None:
    global text
    needle = f"limit_req zone={zone}"
    # Find location block starting with block_start and inject after "{"
    idx = text.find(block_start)
    if idx < 0:
        return
    brace = text.find("{", idx)
    if brace < 0:
        return
    window = text[brace: brace + 200]
    if needle in window:
        return
    inject = f"\n        limit_req zone={zone} burst={burst} nodelay;\n        limit_req_status 429;"
    text = text[: brace + 1] + inject + text[brace + 1 :]

# Prefer specific locations first (order in file matters less than presence)
ensure_limit("location ^~ /admin", "degen_admin", "15")
ensure_limit("location /admin", "degen_admin", "15")
ensure_limit("location ^~ /feedback", "degen_feedback", "5")
ensure_limit("location /feedback", "degen_feedback", "5")
ensure_limit("location ~* /api/predict", "degen_api", "10")
# General location / last — only the bare "location / {" 
# Avoid matching /static/
import re
m = re.search(r"(location\s+/\s*\{)", text)
if m and "limit_req zone=degen_general" not in text[m.start(): m.start() + 180]:
    brace = m.end() - 1
    inject = "\n        limit_req zone=degen_general burst=40 nodelay;\n        limit_req_status 429;"
    text = text[: brace + 1] + inject + text[brace + 1 :]

if text != orig:
    backup = path.with_suffix(path.suffix + ".bak-hardening")
    backup.write_text(orig, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    print(f"patched {path} (backup {backup})")
else:
    print(f"no changes needed in {path}")
PY

echo "==> nginx test + reload"
sudo nginx -t
sudo systemctl reload nginx
echo "==> fail2ban status"
sudo fail2ban-client status || true
echo "==> Done"
