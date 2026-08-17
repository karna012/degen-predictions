#!/usr/bin/env bash
# One-shot Telegram channel test. Run from repo root:
#   bash deploy/send_telegram_test.sh
set -euo pipefail
HOST="ubuntu@52.201.204.110"
KEY="deploy/degen-predictions-key.pem"
cd "$(dirname "$0")/.."

ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/Degen_predictions/webapp
set -a
# shellcheck disable=SC1091
. ../deploy/production.env
set +a
../venv/bin/python3 - <<'PY'
import os
import sys
from datetime import datetime, timezone

import requests

token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip().replace(" ", "")
if not token or not chat:
    print("missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
    sys.exit(1)

text = (
    "Degen Predictions — test ping\n"
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    + "\nhttps://degenpredictions.com/todays-picks"
)
r = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": False,
    },
    timeout=30,
)
print("status", r.status_code)
print("ok", r.ok)
if not r.ok:
    print(r.text[:300])
    sys.exit(1)
mid = (r.json().get("result") or {}).get("message_id")
print("message_id", mid)
PY
REMOTE
