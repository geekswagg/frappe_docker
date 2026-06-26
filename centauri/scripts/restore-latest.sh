#!/bin/bash
# Restore latest Restic snapshot — used during Azure failover when replication is stale
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/centauri/frappe_docker}"
RESTORE_DIR="${RESTORE_DIR:-/opt/centauri/restore-staging}"
SITE="${FRAPPE_SITE:-erp.centauri.io}"
PROJECT="${COMPOSE_PROJECT:-centauri}"

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-azure:restic-repo:/}"
export AZURE_ACCOUNT_NAME="${AZURE_ACCOUNT_NAME:?set AZURE_ACCOUNT_NAME}"
export AZURE_ACCOUNT_KEY="${AZURE_ACCOUNT_KEY:?set AZURE_ACCOUNT_KEY}"
export RESTIC_PASSWORD="${RESTIC_PASSWORD:?set RESTIC_PASSWORD}"

log() { echo "$(date -Iseconds) $*"; }

log "=== Restore from Restic snapshot ==="

mkdir -p "$RESTORE_DIR"

# 1. Restore latest snapshot
log "Restoring latest Restic snapshot..."
restic restore latest \
  --tag "centauri-erpnext" \
  --target "$RESTORE_DIR"

BACKUP_PATH=$(find "$RESTORE_DIR" -name "*.sql.gz" | sort -t_ -k1 | tail -1)
log "Found backup: $BACKUP_PATH"

# 2. Stop ERPNext services (keep db running for restore)
log "Stopping ERPNext app services..."
docker compose -p "$PROJECT" stop backend frontend websocket queue-short queue-long scheduler || true

# 3. Restore database
log "Restoring database..."
docker compose -p "$PROJECT" -f "$COMPOSE_DIR/compose.yaml" exec -T db \
  mariadb -uroot -p"${DB_PASSWORD:?set DB_PASSWORD}" < "$BACKUP_PATH"

# 4. Restore files into the sites volume
FILES_BACKUP=$(find "$RESTORE_DIR" -name "*files.tar" | sort | tail -1)
if [ -n "$FILES_BACKUP" ]; then
  log "Restoring site files..."
  docker compose -p "$PROJECT" \
    run --rm --no-deps \
    -v "$RESTORE_DIR:/restore-in" \
    backend \
    bash -c "
      tar -xf /restore-in/$(basename "$FILES_BACKUP") \
        -C /home/frappe/frappe-bench/sites/$SITE/
    "
fi

# 5. Disable read_only on MariaDB (promoting from replica)
docker compose -p "$PROJECT" exec -T db \
  mariadb -uroot -p"${DB_PASSWORD}" \
  -e "SET GLOBAL read_only = OFF; SET GLOBAL super_read_only = OFF;"

log "=== Restore complete ==="
