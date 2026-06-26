#!/bin/bash
# Centauri failback: drain Azure ERPNext, update DNS to laptop, deallocate VM.
# Called by heartbeat.sh when primary comes back online.
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
# Time to drain in-flight requests before switching DNS (seconds)
DRAIN_SECONDS="${DRAIN_SECONDS:-180}"

log() { echo "$(date -Iseconds) [FAILBACK] $*"; }

log "=== Failback initiated ==="

# 1. Wait for primary to fully stabilise before switching back
log "Waiting ${DRAIN_SECONDS}s for primary to stabilise..."
sleep "$DRAIN_SECONDS"

# 2. Resolve current laptop IP (use DDNS hostname or ipify)
PRIMARY_IP="${PRIMARY_IP:-$(curl -sf https://api.ipify.org)}"
log "Primary IP: $PRIMARY_IP"

# 3. Update DNS back to laptop
RECORD_ID=$(curl -sf \
  "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=${DNS_RECORD_NAME}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  | jq -r '.result[0].id')

curl -sf -X PUT \
  "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${RECORD_ID}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"${DNS_RECORD_NAME}\",\"content\":\"${PRIMARY_IP}\",\"ttl\":60,\"proxied\":false}" \
  > /dev/null

log "DNS updated: ${DNS_RECORD_NAME} → ${PRIMARY_IP}"

# 4. Allow DNS to propagate and drain Azure connections
log "Draining Azure connections (60s)..."
sleep 60

# 5. Sync any data written during failover back to the primary via Restic
log "Triggering final backup from Azure before shutdown..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "${SSH_USER}@${AZURE_VM_PUBLIC_IP}" \
  "bash $COMPOSE_DIR/centauri/scripts/backup.sh" || log "WARN: Final Azure backup failed — check manually"

# 6. Stop ERPNext on Azure (keep DB/Redis running briefly for safety, then full stop)
log "Stopping ERPNext services on Azure..."
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "${SSH_USER}@${AZURE_VM_PUBLIC_IP}" \
  "docker compose -p centauri stop backend frontend websocket queue-short queue-long scheduler"

sleep 30

ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "${SSH_USER}@${AZURE_VM_PUBLIC_IP}" \
  "docker compose -p centauri down"

# 7. Deallocate VM (stop compute billing)
log "Deallocating Azure VM..."
az vm deallocate --resource-group "$VM_RESOURCE_GROUP" --name "$VM_NAME"

log "=== Failback complete. Azure VM deallocated. ==="
