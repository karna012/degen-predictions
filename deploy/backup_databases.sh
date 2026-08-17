#!/usr/bin/env bash
# Application-consistent SQLite backups, complementing the daily EBS
# snapshots (DLM): EBS snapshots are disk-level (crash-consistent) and only
# protect against instance/volume loss -- they capture whatever state the
# files happened to be in, including a torn write. sqlite3's ".backup"
# uses SQLite's own backup API, which is safe to run against a live
# database and always produces a structurally valid copy regardless of
# concurrent writes. This is what would have made the Aug 17 combo_results.db
# corruption a non-event: restore the last backup instead of a manual
# row-by-row recovery.
set -uo pipefail

WEBAPP_DIR="/home/ubuntu/Degen_predictions/webapp"
BACKUP_DIR="/home/ubuntu/Degen_predictions/deploy/db_backups"
RETENTION_DAYS=7
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$BACKUP_DIR"

for db in analytics.db combo_results.db feedback.db; do
    src="$WEBAPP_DIR/$db"
    [ -f "$src" ] || continue
    dest="$BACKUP_DIR/${db%.db}_${STAMP}.db"
    if sqlite3 "$src" ".backup '$dest'" 2>>"$BACKUP_DIR/backup.log"; then
        gzip -f "$dest"
        echo "$(date -u +%FT%TZ) backed up $db -> $(basename "$dest").gz" >> "$BACKUP_DIR/backup.log"
    else
        echo "$(date -u +%FT%TZ) FAILED to back up $db" >> "$BACKUP_DIR/backup.log"
    fi
done

# Prune backups older than RETENTION_DAYS.
find "$BACKUP_DIR" -name "*.db.gz" -mtime "+${RETENTION_DAYS}" -delete

exit 0
