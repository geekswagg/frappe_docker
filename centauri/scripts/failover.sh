#!/bin/bash
# Centauri failover: start Azure VM, start ERPNext stack, update DNS.
# Called by heartbeat.sh when primary is unreachable.
set -euo pipefail

VM_RESOURCE_GROUP="${VM_RESOURCE_GROUP:?set VM_RESOURCE_GROUP}"
VM_NAME="${VM_NAME:?set VM_NAME}"
CF_API_TOKEN="${CF_API_TOKEN:?set CF_API_TOKEN}"
CF_ZONE_ID="${CF_ZONE_ID:?set CF_ZONE_ID}"
AZURE_VM_PUBLIC_IP="${AZURE_VM_PUBLIC_IP:?set AZURE_VM_PUBLIC_IP}"
DNS_RECORD_NAME="${DNS_RECORD_NAME:-erp.centauri.io}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa}"
SSH_USER="${SSH_USER:-centauri}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/centauri/frappe_docker}"
MAX_REPLICATION_LAG="${MAX_REPLICATION_LAG:-300}"

log() { echo "$(date -Iseconds) [FAILOVER] $*"; }

log "=== Failover initiated ==="

# 1. Start Azure VM
log "Starting Azure VM..."
az vm start --resource-group "$VM_RESOURCE_GROUP" --name "$VM_NAME"
az vm wait \
  --resource-group "$VM_RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --custom "instanceView.statuses[?code=='PowerState/running']"
log "VM running"

# 2. Give SSH daemon time to start
sleep 20

# 3. SSH into VM: check replication lag, start ERPNext
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "${SSH_USER}@${AZURE_VM_PUBLIC_IP}" \
  COMPOSE_DIR="$COMPOSE_DIR" MAX_LAG="$MAX_REPLICATION_LAG" \
  'bash -s' << 'REMOTE'
    set -euo pipefail
    DB_PASSWORD="$(grep DB_PASSWORD "$COMPOSE_DIR/.env" | cut -d= -f2)"
    PROJECT="centauri"

    LAG=$(docker compose -p "$PROJECT" exec -T db \
      mariadb -uroot -p"$DB_PASSWORD" -se \
      "SHOW SLAVE STATUS\G" 2>/dev/null \
      | grep "Seconds_Behind_Master" | awk '{print $2}' || echo "NULL")

    echo "Replication lag: $LAG seconds"

    if [ "$LAG" = "NULL" ] || [ "${LAG:-99999}" -gt "$MAX_LAG" ]; then
      echo "Lag too high or broken — restoring from Restic backup"
      bash "$COMPOSE_DIR/centauri/scripts/restore-latest.sh"
    else
      echo "Promoting replica (lag=${LAG}s)"
      docker compose -p "$PROJECT" exec -T db \
        mariadb -uroot -p"$DB_PASSWORD" \
        -e "STOP SLAVE; RESET SLAVE ALL; SET GLOBAL read_only=OFF; SET GLOBAL super_read_only=OFF;"
    fi

    # Start full ERPNext stack
    docker compose \
      -f "$COMPOSE_DIR/compose.yaml" \
      -f "$COMPOSE_DIR/overrides/compose.mariadb.yaml" \
      -f "$COMPOSE_DIR/overrides/compose.redis.yaml" \
      -f "$COMPOSE_DIR/overrides/compose.https.yaml" \
      --env-file "$COMPOSE_DIR/.env" \
      -p "$PROJECT" \
      up -d
    echo "ERPNext stack started on Azure"
REMOTE

# 4. Wait for ERPNext to be healthy before switching DNS
log "Waiting for ERPNext health check..."
for i in $(seq 1 12); do
  if curl -sf --max-time 10 "https://${AZURE_VM_PUBLIC_IP}/api/method/ping" > /dev/null 2>&1; then
    log "ERPNext healthy on Azure"
    break
  fi
  log "Attempt $i/12 — not ready yet, waiting 15s..."
  sleep 15
done

# 5. Update DNS to Azure VM IP
RECORD_ID=$(curl -sf \
  "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=${DNS_RECORD_NAME}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  | jq -r '.result[0].id')

curl -sf -X PUT \
  "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${RECORD_ID}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"${DNS_RECORD_NAME}\",\"content\":\"${AZURE_VM_PUBLIC_IP}\",\"ttl\":60,\"proxied\":false}" \
  > /dev/null

log "DNS updated: ${DNS_RECORD_NAME} → ${AZURE_VM_PUBLIC_IP}"
log "=== Failover complete ==="
