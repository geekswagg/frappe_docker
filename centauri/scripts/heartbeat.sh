#!/bin/bash
# Centauri heartbeat monitor — runs in Azure Container Instance.
# Polls the primary ERPNext, triggers failover/failback via Cloudflare DNS + Azure VM.
set -euo pipefail

PRIMARY_URL="${PRIMARY_URL:?set PRIMARY_URL}"          # e.g. https://erp.centauri.io/api/method/ping
VM_RESOURCE_GROUP="${VM_RESOURCE_GROUP:?}"
VM_NAME="${VM_NAME:?}"
CF_API_TOKEN="${CF_API_TOKEN:?}"
CF_ZONE_ID="${CF_ZONE_ID:?}"
AZURE_VM_PUBLIC_IP="${AZURE_VM_PUBLIC_IP:?}"
DNS_RECORD_NAME="${DNS_RECORD_NAME:-erp.centauri.io}"
CHECK_INTERVAL="${CHECK_INTERVAL:-60}"
MAX_FAILS="${MAX_FAILS:-3}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

FAIL_COUNT=0
FAILOVER_ACTIVE=false

log() { echo "$(date -Iseconds) [$1] ${*:2}"; }

cf_get_record_id() {
  curl -sf "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=${DNS_RECORD_NAME}" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    | jq -r '.result[0].id'
}

cf_update_dns() {
  local ip="$1"
  local record_id
  record_id=$(cf_get_record_id)
  curl -sf -X PUT \
    "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${record_id}" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "{\"type\":\"A\",\"name\":\"${DNS_RECORD_NAME}\",\"content\":\"${ip}\",\"ttl\":60,\"proxied\":false}" \
    > /dev/null
  log INFO "DNS updated → $ip"
}

while true; do
  if curl -sf --max-time 10 "$PRIMARY_URL" > /dev/null 2>&1; then
    FAIL_COUNT=0
    if [ "$FAILOVER_ACTIVE" = "true" ]; then
      log INFO "Primary is back — initiating failback"
      bash "${SCRIPT_DIR}/failback.sh"
      FAILOVER_ACTIVE=false
    fi
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    log WARN "Primary unreachable (${FAIL_COUNT}/${MAX_FAILS})"
    if [ "$FAIL_COUNT" -ge "$MAX_FAILS" ] && [ "$FAILOVER_ACTIVE" = "false" ]; then
      log WARN "Threshold reached — initiating failover"
      bash "${SCRIPT_DIR}/failover.sh"
      FAILOVER_ACTIVE=true
    fi
  fi
  sleep "$CHECK_INTERVAL"
done
