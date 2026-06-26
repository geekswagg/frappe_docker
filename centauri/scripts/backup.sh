#!/bin/bash
# Centauri ERPNext backup: bench backup → stage files → Restic snapshot → Azure Blob
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/centauri/frappe_docker}"
BACKUP_DIR="${BACKUP_DIR:-/opt/centauri/backup-staging}"
SITE="${FRAPPE_SITE:-erp.centauri.io}"
PROJECT="${COMPOSE_PROJECT:-centauri}"
LOG_FILE="/var/log/centauri-backup.log"

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-azure:restic-repo:/}"
export AZURE_ACCOUNT_NAME="${AZURE_ACCOUNT_NAME:?set AZURE_ACCOUNT_NAME}"
export AZURE_ACCOUNT_KEY="${AZURE_ACCOUNT_KEY:?set AZURE_ACCOUNT_KEY}"
export RESTIC_PASSWORD="${RESTIC_PASSWORD:?set RESTIC_PASSWORD}"

log() { echo "$(date -Iseconds) $*" | tee -a "$LOG_FILE"; }

log "=== Backup started ==="

mkdir -p "$BACKUP_DIR/backups"

# 1. Frappe bench backup (SQL dump + private/public files)
log "Running bench backup..."
docker compose -p "$PROJECT" -f "$COMPOSE_DIR/compose.yaml" exec -T backend \
  bench --site "$SITE" backup --with-files

# 2. Copy backup files out of the container volume to staging directory
log "Copying backup files to staging..."
docker compose -p "$PROJECT" -f "$COMPOSE_DIR/compose.yaml" \
  run --rm --no-deps \
  -v "$BACKUP_DIR:/backup-out" \
  backend \
  bash -c "
    src=\"/home/frappe/frappe-bench/sites/$SITE/private/backups\"
    dst=\"/backup-out/backups\"
    mkdir -p \"\$dst\"
    # Copy only the latest backup set (most recent by mtime)
    ls -t \"\$src\"/*.sql.gz 2>/dev/null | head -1 | xargs -I{} cp {} \"\$dst/\"
    ls -t \"\$src\"/*files.tar 2>/dev/null | head -1 | xargs -I{} cp {} \"\$dst/\" || true
    ls -t \"\$src\"/*database.sql.gz 2>/dev/null | head -1 | xargs -I{} cp {} \"\$dst/\" || true
  "

# 3. Restic snapshot
log "Creating Restic snapshot..."
restic backup "$BACKUP_DIR" \
  --tag "centauri-erpnext" \
  --tag "site-$SITE" \
  --tag "host-$(hostname)" \
  --exclude "*.tmp"

# 4. Retention policy
log "Pruning old snapshots..."
restic forget \
  --keep-hourly 24 \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 3 \
  --prune

log "=== Backup complete ==="
