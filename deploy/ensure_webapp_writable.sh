#!/usr/bin/env bash
# Make sure gunicorn (user ubuntu) can write SQLite DBs + journals under webapp/.
# Windows-built tar bundles often leave files owned by numeric UIDs (e.g. 197609),
# which makes analytics.db appear "readonly" and used to 500 every page.
#
# Safe to run anytime (idempotent). Prefer calling after every deploy extract.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBAPP="${ROOT}/webapp"
OWNER="${DEPLOY_OWNER:-ubuntu}"
GROUP="${DEPLOY_GROUP:-ubuntu}"

if [[ ! -d "$WEBAPP" ]]; then
  echo "ensure_webapp_writable: missing $WEBAPP" >&2
  exit 1
fi

# Directory must be writable for SQLite -wal/-journal sidecars.
sudo chown -R "${OWNER}:${GROUP}" "$WEBAPP"
sudo find "$WEBAPP" -type d -exec chmod u+rwx {} +
sudo find "$WEBAPP" -type f -exec chmod u+rw {} +

# Telegram/X slip PNGs live under data/daily_digest/cards (not webapp/).
DIGEST_CARDS="${ROOT}/data/daily_digest"
if [[ -d "$DIGEST_CARDS" ]] || mkdir -p "$DIGEST_CARDS"; then
  sudo chown -R "${OWNER}:${GROUP}" "$DIGEST_CARDS"
  sudo chmod -R u+rwX "$DIGEST_CARDS"
fi

# Last-known-good live scoreboard snapshots (survives gunicorn restart).
LIVE_FEEDS="${ROOT}/data/live_feeds"
mkdir -p "$LIVE_FEEDS"
sudo chown -R "${OWNER}:${GROUP}" "$LIVE_FEEDS"
sudo chmod -R u+rwX "$LIVE_FEEDS"

# Admin Sources panel (Telegram/API keys + live source-health probes).
ADMIN_DATA="${ROOT}/data/admin"
mkdir -p "$ADMIN_DATA"
sudo chown -R "${OWNER}:${GROUP}" "$ADMIN_DATA"
sudo chmod -R u+rwX "$ADMIN_DATA"

# Explicit DB files (even if currently missing, touch-safe skip)
for db in analytics.db users.db feedback.db combo_results.db; do
  if [[ -e "$WEBAPP/$db" ]]; then
    sudo chown "${OWNER}:${GROUP}" "$WEBAPP/$db"
    sudo chmod u+rw "$WEBAPP/$db"
  fi
  # Sidecars from interrupted writes
  for side in "$WEBAPP/${db}-journal" "$WEBAPP/${db}-wal" "$WEBAPP/${db}-shm"; do
    if [[ -e "$side" ]]; then
      sudo chown "${OWNER}:${GROUP}" "$side"
      sudo chmod u+rw "$side"
    fi
  done
done

# Static assets must stay world-readable for nginx.
if [[ -d "$WEBAPP/static" ]]; then
  sudo chmod -R a+rX "$WEBAPP/static"
fi

echo "ensure_webapp_writable: ok (${OWNER}:${GROUP} owns ${WEBAPP})"
