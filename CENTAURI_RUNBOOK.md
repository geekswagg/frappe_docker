# Centauri ERPNext Adoption Runbook

**Stack:** ERPNext v16.25.0 · Frappe v16 · MariaDB 11.8 · Redis 8.6  
**Target:** Ubuntu Desktop 26.04 LTS (primary) + Azure (failover)  
**Pattern:** API-heavy integrations with hot-standby failover

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Phase 1 — Local Ubuntu 26.04 LTS Setup](#3-phase-1--local-ubuntu-2604-lts-setup)
4. [Phase 2 — Azure Failover Environment](#4-phase-2--azure-failover-environment)
5. [Phase 3 — Data Replication Strategy](#5-phase-3--data-replication-strategy)
6. [Phase 4 — Failover Automation](#6-phase-4--failover-automation)
7. [Phase 5 — API Configuration for Integrations](#7-phase-5--api-configuration-for-integrations)
8. [Phase 6 — Backup & Recovery](#8-phase-6--backup--recovery)
9. [Day-2 Operations](#9-day-2-operations)
10. [Upgrade Path](#10-upgrade-path)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  CENTAURI ERPNEXT — HYBRID TOPOLOGY                         │
│                                                             │
│  ┌─────────────────────────┐      ┌──────────────────────┐ │
│  │  PRIMARY (Laptop)        │      │  FAILOVER (Azure)    │ │
│  │  Ubuntu Desktop 26.04   │      │  Azure VM B2s        │ │
│  │                         │      │                      │ │
│  │  ERPNext stack          │      │  ERPNext stack       │ │
│  │  (Gunicorn + Nginx      │      │  (same compose)      │ │
│  │   + Websocket + RQ)     │      │                      │ │
│  │                         │      │  ┌─────────────────┐ │ │
│  │  MariaDB 11.8 (master)  │─────▶│  │ MariaDB replica │ │ │
│  │  Redis 8.6              │      │  └─────────────────┘ │ │
│  │                         │      │  Redis 8.6           │ │
│  │  Restic ──────────────────────▶│  Azure Blob Storage  │ │
│  └────────────┬────────────┘      └──────────┬───────────┘ │
│               │                              │             │
│               └──────────┬───────────────────┘             │
│                          ▼                                  │
│              ┌───────────────────────┐                     │
│              │  Azure DNS / CF DNS   │                     │
│              │  erp.centauri.io      │                     │
│              │  api.centauri.io      │                     │
│              └───────────────────────┘                     │
└─────────────────────────────────────────────────────────────┘

FAILOVER LOGIC:
  Heartbeat monitor (Azure) pings local every 60s
  → Laptop offline for 3 min → Azure Logic App starts containers
  → DNS A-record updated to Azure public IP
  → Laptop comes back online → drain Azure → switch DNS back
```

### Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Replication method | MariaDB semi-sync binlog + Restic full backup | Near-real-time with a full-backup safety net |
| Failover trigger | Azure Logic App heartbeat monitor | No extra infra on laptop side |
| DNS | Cloudflare (free) or Azure DNS | Sub-60s TTL propagation |
| API auth | Frappe API keys + OAuth2 | Stateless, revocable, per-integration |
| Compose style | Base `compose.yaml` + layered overrides | Keeps parity between local and Azure |

---

## 2. Prerequisites

### 2.1 Local Machine

- Ubuntu Desktop 26.04 LTS (fresh install or upgrade path)
- Minimum specs: 8 GB RAM, 4 CPU cores, 100 GB SSD
- Static LAN IP or DDNS entry (e.g. `home.centauri.io`) for Azure to reach
- Router: port-forward 443 → laptop (or use Cloudflare Tunnel if NAT is restrictive)
- Domain name with API access (Cloudflare recommended)

### 2.2 Azure

- Azure subscription (Pay-As-You-Go is fine)
- Resource group: `centauri-erpnext-rg`
- Services provisioned in Phase 2:
  - VM: Standard B2s (2 vCPU, 4 GB RAM) — stopped by default to save cost
  - Storage account for Restic backups and binlog archives
  - Logic App for heartbeat + failover orchestration
  - Azure DNS zone or reliance on Cloudflare API

### 2.3 Tooling (both machines)

```bash
# Docker Engine (not Desktop)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # re-login after this

# Docker Compose plugin
sudo apt-get install -y docker-compose-plugin

# Azure CLI (laptop only for setup)
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Restic (backup tool — already in the ERPNext production image)
sudo apt-get install -y restic
```

---

## 3. Phase 1 — Local Ubuntu 26.04 LTS Setup

### 3.1 Clone and Configure

```bash
git clone https://github.com/geekswagg/frappe_docker.git /opt/centauri/frappe_docker
cd /opt/centauri/frappe_docker

# Copy and edit env
cp example.env .env
```

Edit `.env` — minimum changes:

```dotenv
ERPNEXT_VERSION=v16.25.0

# Set a strong password
DB_PASSWORD=<strong-random-password>

# Tune for your CPU: (2 * cores) + 1
GUNICORN_WORKERS=9
GUNICORN_THREADS=4
GUNICORN_TIMEOUT=120

# Your domain
SITES_RULE=Host(`erp.centauri.io`)
LETSENCRYPT_EMAIL=collins.kibagendi@technobraingbs.com

# Increase for large file attachments / imports
CLIENT_MAX_BODY_SIZE=100m
PROXY_READ_TIMEOUT=300s
```

### 3.2 Compose Stack for Local

Create `/opt/centauri/frappe_docker/centauri-local.env` as a stack-specific override pointer, then launch with all relevant overrides:

```bash
cd /opt/centauri/frappe_docker

docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.https.yaml \
  -f overrides/compose.backup-cron.yaml \
  --env-file .env \
  -p centauri \
  up -d
```

This gives you:
- ERPNext (Gunicorn + Nginx + Websocket + RQ workers + Scheduler)
- MariaDB 11.8 (containerized, with healthchecks)
- Redis 8.6 (separate cache and queue instances)
- Traefik with Let's Encrypt HTTPS
- Ofelia backup cron (every 6 hours)

### 3.3 Create the ERPNext Site

```bash
# Wait for configurator to finish (~ 2 min on first boot)
docker compose -p centauri logs -f configurator

# Create site
docker compose -p centauri exec backend \
  bench new-site erp.centauri.io \
    --db-root-password "$DB_PASSWORD" \
    --admin-password "<admin-password>" \
    --install-app erpnext

# Enable scheduler
docker compose -p centauri exec backend \
  bench --site erp.centauri.io enable-scheduler
```

### 3.4 Persist MariaDB Configuration for Replication

Add a `custom-mariadb.cnf` file and mount it. Create `/opt/centauri/mariadb-master.cnf`:

```ini
[mysqld]
# Replication identity
server-id          = 1
log_bin            = /var/lib/mysql/mysql-bin
binlog_format      = ROW
binlog_row_image   = FULL
expire_logs_days   = 7

# For semi-synchronous replication
plugin-load-add    = semisync_master.so
rpl_semi_sync_master_enabled = 1
rpl_semi_sync_master_timeout = 5000   # ms — fall back to async after 5s

# Performance
innodb_flush_log_at_trx_commit = 1
sync_binlog                    = 1
```

Create a compose override for this config at `/opt/centauri/frappe_docker/overrides/compose.mariadb-replication.yaml`:

```yaml
services:
  db:
    volumes:
      - /opt/centauri/mariadb-master.cnf:/etc/mysql/conf.d/replication.cnf:ro
    ports:
      - "127.0.0.1:3306:3306"   # bind locally only; expose via SSH tunnel to Azure
```

Add this override to the launch command above.

### 3.5 Create Replication User in MariaDB

```bash
docker compose -p centauri exec db \
  mariadb -uroot -p"$DB_PASSWORD" -e "
    CREATE USER IF NOT EXISTS 'replicator'@'%'
      IDENTIFIED BY '<replication-password>';
    GRANT REPLICATION SLAVE ON *.* TO 'replicator'@'%';
    FLUSH PRIVILEGES;
  "
```

### 3.6 Enable Systemd Auto-start on Boot

Create `/etc/systemd/system/centauri-erpnext.service`:

```ini
[Unit]
Description=Centauri ERPNext Stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/centauri/frappe_docker
ExecStart=/usr/bin/docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.https.yaml \
  -f overrides/compose.backup-cron.yaml \
  -f overrides/compose.mariadb-replication.yaml \
  --env-file .env \
  -p centauri \
  up -d
ExecStop=/usr/bin/docker compose -p centauri down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable centauri-erpnext
```

---

## 4. Phase 2 — Azure Failover Environment

### 4.1 Provision Azure Resources

```bash
az login
RESOURCE_GROUP="centauri-erpnext-rg"
LOCATION="eastus"    # pick the region closest to you
VM_NAME="centauri-erpnext-failover"

# Resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# Storage account for Restic + binlog archives
az storage account create \
  --name centauribackups \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2

# Blob container for Restic repo
az storage container create \
  --name restic-repo \
  --account-name centauribackups

# VM — Standard_B2s: 2 vCPU, 4 GB RAM (~$30/month if always-on, ~$0 when deallocated)
az vm create \
  --resource-group $RESOURCE_GROUP \
  --name $VM_NAME \
  --image Ubuntu2404 \
  --size Standard_B2s \
  --admin-username centauri \
  --generate-ssh-keys \
  --public-ip-sku Standard \
  --os-disk-size-gb 64

# Open HTTPS and ERPNext port
az vm open-port --port 443 --resource-group $RESOURCE_GROUP --name $VM_NAME
az vm open-port --port 8080 --resource-group $RESOURCE_GROUP --name $VM_NAME --priority 1001

# Deallocate the VM — it runs only during failover
az vm deallocate --resource-group $RESOURCE_GROUP --name $VM_NAME
```

### 4.2 Bootstrap the Azure VM

SSH in (while it's running for setup), then mirror the local setup:

```bash
# On the Azure VM
sudo apt-get update && sudo apt-get install -y git
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker centauri

git clone https://github.com/geekswagg/frappe_docker.git /opt/centauri/frappe_docker
cd /opt/centauri/frappe_docker
cp example.env .env
# Edit .env with SAME values as local — SAME DB_PASSWORD, site name, etc.
```

Create `/opt/centauri/mariadb-replica.cnf` on the Azure VM:

```ini
[mysqld]
server-id              = 2
relay_log              = /var/lib/mysql/relay-bin
log_bin                = /var/lib/mysql/mysql-bin
binlog_format          = ROW
read_only              = 1           # replica is read-only until promoted

plugin-load-add        = semisync_slave.so
rpl_semi_sync_slave_enabled = 1
```

Create `overrides/compose.mariadb-replica.yaml`:

```yaml
services:
  db:
    volumes:
      - /opt/centauri/mariadb-replica.cnf:/etc/mysql/conf.d/replication.cnf:ro
```

Create the systemd unit on Azure (same as local but without the mariadb-replication override, using the replica override instead):

```bash
# Start initially WITHOUT ERPNext — just DB + Redis to establish replication
docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.mariadb-replica.yaml \
  --env-file .env \
  -p centauri \
  up -d db redis-cache redis-queue
```

### 4.3 Bootstrap MariaDB Replication

From the local machine, take a consistent snapshot and seed the replica:

```bash
# 1. On local: dump with master coordinates
docker compose -p centauri exec db \
  mariadb-dump \
    -uroot -p"$DB_PASSWORD" \
    --all-databases \
    --master-data=2 \
    --single-transaction \
    --quick \
  > /opt/centauri/full-seed.sql.gz   # pipe through gzip in practice

# 2. Transfer to Azure VM
scp /opt/centauri/full-seed.sql.gz centauri@<azure-vm-ip>:/opt/centauri/

# 3. On Azure: import
docker compose -p centauri exec -T db \
  mariadb -uroot -p"$DB_PASSWORD" < /opt/centauri/full-seed.sql.gz

# 4. On Azure: configure slave (extract binlog file/pos from dump header)
BINLOG_FILE=$(grep "MASTER_LOG_FILE" /opt/centauri/full-seed.sql | head -1 | awk -F"'" '{print $2}')
BINLOG_POS=$(grep "MASTER_LOG_POS" /opt/centauri/full-seed.sql | head -1 | awk -F'=' '{print $NF}' | tr -d ';')

docker compose -p centauri exec db \
  mariadb -uroot -p"$DB_PASSWORD" -e "
    CHANGE MASTER TO
      MASTER_HOST='<laptop-public-ip-or-ddns>',
      MASTER_PORT=3306,
      MASTER_USER='replicator',
      MASTER_PASSWORD='<replication-password>',
      MASTER_LOG_FILE='$BINLOG_FILE',
      MASTER_LOG_POS=$BINLOG_POS;
    START SLAVE;
  "

# 5. Verify replication is running
docker compose -p centauri exec db \
  mariadb -uroot -p"$DB_PASSWORD" -e "SHOW SLAVE STATUS\G" \
  | grep -E "Running|Seconds_Behind|Error"
```

**Important:** The laptop's MariaDB port 3306 must be reachable from Azure. Options:
- **Option A (recommended):** Use an SSH reverse tunnel or Cloudflare Tunnel — no public port exposure needed
- **Option B:** Port-forward 3306 on your router with firewall rules restricted to the Azure VM's outbound IP

### 4.4 Deallocate Azure VM After Setup

```bash
az vm deallocate \
  --resource-group centauri-erpnext-rg \
  --name centauri-erpnext-failover
```

The VM is now in a stopped (deallocated) state — no compute cost until failover triggers it.

---

## 5. Phase 3 — Data Replication Strategy

Two complementary layers:

### Layer 1: MariaDB Semi-Synchronous Binlog Replication (Real-time)

- **RPO:** Near-zero (semi-sync ensures at least one replica acknowledgment before commit)
- **RTO:** ~3-5 minutes (Azure VM startup + container boot + DNS propagation)
- **Lag monitoring:** `SHOW SLAVE STATUS\G` → `Seconds_Behind_Master`
- **Replication channel:** SSH tunnel (laptop ↔ Azure via Cloudflare Tunnel or direct SSH port forward)

```bash
# Monitor replication lag (run on Azure VM)
watch -n 30 "docker compose -p centauri exec db \
  mariadb -uroot -p\$DB_PASSWORD -e 'SHOW SLAVE STATUS\G' \
  | grep Seconds_Behind_Master"
```

### Layer 2: Restic Backup to Azure Blob Storage (Full + Incremental)

**Configure Restic on local machine:**

```bash
# Install restic
sudo apt-get install -y restic

# Environment variables (add to /etc/environment or a secrets file)
export RESTIC_REPOSITORY="azure:restic-repo:/"
export AZURE_ACCOUNT_NAME="centauribackups"
export AZURE_ACCOUNT_KEY="<storage-account-key>"
export RESTIC_PASSWORD="<strong-encryption-passphrase>"

# Initialize repository (one-time)
restic init

# Create backup script at /opt/centauri/scripts/backup.sh
```

Create `/opt/centauri/scripts/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail

COMPOSE_DIR="/opt/centauri/frappe_docker"
BACKUP_DIR="/opt/centauri/backup-staging"
SITE="erp.centauri.io"

export RESTIC_REPOSITORY="azure:restic-repo:/"
export AZURE_ACCOUNT_NAME="centauribackups"
export AZURE_ACCOUNT_KEY="$(cat /run/secrets/azure-storage-key)"
export RESTIC_PASSWORD="$(cat /run/secrets/restic-password)"

mkdir -p "$BACKUP_DIR"

# 1. Bench backup (files + database dump via frappe)
docker compose -p centauri -f "$COMPOSE_DIR/compose.yaml" exec -T backend \
  bench --site "$SITE" backup --with-files

# 2. Copy bench backup to staging
docker compose -p centauri -f "$COMPOSE_DIR/compose.yaml" \
  run --rm --no-deps \
  -v "$BACKUP_DIR:/backup-out" \
  backend \
  bash -c "cp -r /home/frappe/frappe-bench/sites/$SITE/private/backups /backup-out/"

# 3. Restic snapshot
restic backup "$BACKUP_DIR" \
  --tag "centauri-erpnext" \
  --tag "$(hostname)" \
  --exclude "*.tmp"

# 4. Forget old snapshots (keep last 24 hourly, 7 daily, 4 weekly)
restic forget \
  --keep-hourly 24 \
  --keep-daily 7 \
  --keep-weekly 4 \
  --prune

echo "Backup complete: $(date -Iseconds)"
```

```bash
chmod +x /opt/centauri/scripts/backup.sh

# Cron: every 2 hours
echo "0 */2 * * * root /opt/centauri/scripts/backup.sh >> /var/log/centauri-backup.log 2>&1" \
  | sudo tee /etc/cron.d/centauri-backup
```

### Replication Gap Handling (Failover Seed)

When binlog replication is behind (e.g. laptop was off for a while), the Azure failover script (Phase 4) will:
1. Stop the replica
2. Restore from the latest Restic snapshot
3. Re-enable ERPNext services
4. This is the fallback when `Seconds_Behind_Master > 300`

---

## 6. Phase 4 — Failover Automation

### 6.1 Heartbeat Monitor Script (Runs on Azure Logic App)

Create an Azure Logic App with a Recurrence trigger (every 60 seconds) that calls an HTTP endpoint on your laptop. If it fails 3 consecutive times, it triggers failover.

**Alternative (simpler):** A small script on an always-on Azure Container Instance (very cheap, ~$3/month):

```bash
# Deploy heartbeat monitor as Azure Container Instance
az container create \
  --resource-group centauri-erpnext-rg \
  --name centauri-heartbeat \
  --image mcr.microsoft.com/azure-cli:latest \
  --restart-policy Always \
  --command-line "/bin/bash /scripts/heartbeat.sh" \
  --environment-variables \
    PRIMARY_URL="https://erp.centauri.io/api/method/ping" \
    VM_RESOURCE_GROUP="centauri-erpnext-rg" \
    VM_NAME="centauri-erpnext-failover" \
    CF_API_TOKEN="<cloudflare-api-token>" \
    CF_ZONE_ID="<cloudflare-zone-id>" \
    AZURE_VM_PUBLIC_IP="<azure-vm-public-ip>"
```

`heartbeat.sh` (stored in Azure Storage, mounted into ACI):

```bash
#!/bin/bash
FAIL_COUNT=0
MAX_FAILS=3
CHECK_INTERVAL=60
FAILOVER_ACTIVE=false

while true; do
  if curl -sf --max-time 10 "$PRIMARY_URL" > /dev/null 2>&1; then
    FAIL_COUNT=0
    if [ "$FAILOVER_ACTIVE" = "true" ]; then
      echo "$(date): Primary back online — triggering failback"
      bash /scripts/failback.sh
      FAILOVER_ACTIVE=false
    fi
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "$(date): Primary unreachable (attempt $FAIL_COUNT/$MAX_FAILS)"
    if [ "$FAIL_COUNT" -ge "$MAX_FAILS" ] && [ "$FAILOVER_ACTIVE" = "false" ]; then
      echo "$(date): Triggering failover"
      bash /scripts/failover.sh
      FAILOVER_ACTIVE=true
    fi
  fi
  sleep $CHECK_INTERVAL
done
```

### 6.2 Failover Script (`failover.sh`)

```bash
#!/bin/bash
set -euo pipefail
echo "=== FAILOVER INITIATED: $(date -Iseconds) ==="

# 1. Start Azure VM
az vm start \
  --resource-group "$VM_RESOURCE_GROUP" \
  --name "$VM_NAME"

# Wait for VM to be running
az vm wait \
  --resource-group "$VM_RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --custom "instanceView.statuses[?code=='PowerState/running']"

echo "VM started"

# 2. SSH into VM and check replication lag / restore from backup if needed
AZURE_VM_IP=$(az vm show \
  --resource-group "$VM_RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --show-details \
  --query publicIps -o tsv)

ssh -o StrictHostKeyChecking=no centauri@"$AZURE_VM_IP" bash << 'REMOTE_EOF'
  cd /opt/centauri/frappe_docker

  # Check replication lag
  LAG=$(docker compose -p centauri exec -T db \
    mariadb -uroot -p"$DB_PASSWORD" -se \
    "SHOW SLAVE STATUS\G" 2>/dev/null \
    | grep "Seconds_Behind_Master" | awk '{print $2}')

  if [ "$LAG" = "NULL" ] || [ "${LAG:-999}" -gt 300 ]; then
    echo "Replication lag too high or broken ($LAG s) — restoring from Restic backup"
    bash /opt/centauri/scripts/restore-latest.sh
  else
    echo "Replication lag acceptable ($LAG s) — promoting replica"
    docker compose -p centauri exec -T db \
      mariadb -uroot -p"$DB_PASSWORD" -e "STOP SLAVE; RESET SLAVE ALL;"
    docker compose -p centauri exec -T db \
      mariadb -uroot -p"$DB_PASSWORD" -e "SET GLOBAL read_only = 0;"
  fi

  # 3. Start full ERPNext stack
  docker compose \
    -f compose.yaml \
    -f overrides/compose.mariadb.yaml \
    -f overrides/compose.redis.yaml \
    -f overrides/compose.https.yaml \
    --env-file .env \
    -p centauri \
    up -d

  echo "ERPNext stack started on Azure"
REMOTE_EOF

# 4. Update DNS (Cloudflare) to point to Azure VM
curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/$(
  curl -s "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=erp.centauri.io" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result[0].id'
)" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"erp.centauri.io\",\"content\":\"${AZURE_VM_PUBLIC_IP}\",\"ttl\":60,\"proxied\":false}"

echo "=== FAILOVER COMPLETE: $(date -Iseconds) === DNS → Azure"
```

### 6.3 Failback Script (`failback.sh`)

```bash
#!/bin/bash
set -euo pipefail
echo "=== FAILBACK INITIATED: $(date -Iseconds) ==="

# 1. Wait for laptop ERPNext to be fully healthy
sleep 120

# 2. Update DNS back to laptop
LAPTOP_IP=$(curl -s https://api.ipify.org)  # or use your DDNS hostname
curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/$(
  curl -s "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=erp.centauri.io" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result[0].id'
)" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"erp.centauri.io\",\"content\":\"${LAPTOP_IP}\",\"ttl\":60,\"proxied\":false}"

# 3. Stop Azure VM (save cost)
# Allow 5 min for any in-flight requests to drain
sleep 300

ssh centauri@"$AZURE_VM_IP" bash << 'REMOTE_EOF'
  cd /opt/centauri/frappe_docker
  docker compose -p centauri stop backend frontend websocket queue-short queue-long scheduler
  echo "ERPNext stack stopped on Azure"
REMOTE_EOF

az vm deallocate \
  --resource-group centauri-erpnext-rg \
  --name centauri-erpnext-failover

echo "=== FAILBACK COMPLETE: $(date -Iseconds) === DNS → Laptop"
```

### 6.4 Pre-configure DNS TTL

Before go-live, set DNS record TTL to 60 seconds so failover DNS changes propagate quickly. Set this at least 24 hours before the first expected switchover so resolvers expire the old long-TTL cached record.

---

## 7. Phase 5 — API Configuration for Integrations

ERPNext v16 exposes a comprehensive REST API. Here is how to configure it for Centauri's API-heavy integration pattern.

### 7.1 Enable API Access

```bash
# Increase API rate limits (run inside backend container)
docker compose -p centauri exec backend \
  bench --site erp.centauri.io set-config -g \
  allow_cors_origin "https://your-integration-domain.com"

# Allow all origins for internal integrations (restrict in production)
docker compose -p centauri exec backend \
  bench --site erp.centauri.io set-config -g \
  allow_cors "*"
```

### 7.2 Create API Key per Integration

In ERPNext UI → **Settings → Users** → select user → **API Access** section → Generate Keys.

Or via bench:

```bash
docker compose -p centauri exec backend \
  bench --site erp.centauri.io execute frappe.core.doctype.user.user.generate_keys \
  --kwargs '{"user": "centauri-integration@technobraingbs.com"}'
```

Store keys in a secrets manager (Azure Key Vault or `pass`):

```bash
# Every integration should have its own ERPNext user with minimal permissions
# Example users:
#   api-crm@centauri.io       → CRM read/write only
#   api-finance@centauri.io   → Accounts read only
#   api-warehouse@centauri.io → Stock read/write
```

### 7.3 Authentication — Two Patterns

**Pattern A: Token-based (preferred for server-to-server)**

```http
GET /api/resource/Sales Order HTTP/1.1
Host: erp.centauri.io
Authorization: token <api_key>:<api_secret>
```

**Pattern B: OAuth2 (preferred for user-facing apps)**

```http
POST /api/method/frappe.integrations.oauth2.get_token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=user@centauri.io&password=xxx&client_id=xxx&client_secret=xxx
```

### 7.4 Core API Endpoints Reference

| Use Case | Method | Endpoint |
|---|---|---|
| List documents | GET | `/api/resource/{DocType}?filters=[["field","=","value"]]&fields=["field1","field2"]` |
| Get document | GET | `/api/resource/{DocType}/{name}` |
| Create document | POST | `/api/resource/{DocType}` |
| Update document | PUT | `/api/resource/{DocType}/{name}` |
| Delete document | DELETE | `/api/resource/{DocType}/{name}` |
| Run server method | POST | `/api/method/{dotted.path.to.method}` |
| Submit document | PUT | `/api/resource/{DocType}/{name}` body: `{"docstatus": 1}` |
| File upload | POST | `/api/method/upload_file` (multipart) |
| Health check | GET | `/api/method/ping` |

### 7.5 Webhook Outbound (ERPNext → Your Systems)

In ERPNext UI → **Settings → Webhooks** → New:

- **DocType:** e.g. `Sales Invoice`
- **DocEvent:** `on_submit`
- **Request URL:** `https://your-system.centauri.io/webhooks/erpnext`
- **Security Secret:** shared HMAC secret
- **Headers:** `Content-Type: application/json`

### 7.6 Increase API Throughput

Edit `.env`:

```dotenv
# Tune for API-heavy load: more workers, fewer threads per worker
GUNICORN_WORKERS=9       # (2 * 4 cores) + 1
GUNICORN_THREADS=2       # keep low when mostly I/O-bound API calls
GUNICORN_TIMEOUT=300     # allow long-running background job APIs
```

Add to Nginx (via `CLIENT_MAX_BODY_SIZE` and `PROXY_READ_TIMEOUT` in `.env`):

```dotenv
CLIENT_MAX_BODY_SIZE=100m
PROXY_READ_TIMEOUT=300s
```

### 7.7 API Gateway (Optional — Recommended for Production)

For fine-grained rate limiting and observability, deploy a lightweight API gateway in front of ERPNext. Options that compose well with this stack:

- **Traefik middleware** (already in the stack): add rate limit middleware to `compose.https.yaml`
- **Kong CE** or **Caddy with rate-limiting** as a separate container

Traefik rate limit example (add to `overrides/compose.https.yaml`):

```yaml
services:
  traefik:
    command:
      # ... existing flags ...
      - "--providers.docker.defaultRule=Host(`erp.centauri.io`)"
    labels:
      - "traefik.http.middlewares.api-ratelimit.ratelimit.average=100"
      - "traefik.http.middlewares.api-ratelimit.ratelimit.burst=50"
      - "traefik.http.routers.erpnext.middlewares=api-ratelimit"
```

---

## 8. Phase 6 — Backup & Recovery

### 8.1 Backup Schedule

| Backup Type | Frequency | Retention | Storage |
|---|---|---|---|
| Frappe bench backup (files + DB dump) | Every 2 hours | 7 days local | Azure Blob via Restic |
| MariaDB binlogs | Continuous (replication) | 7 days | Local + shipped to Azure |
| Restic full snapshot | Nightly | 30 days | Azure Blob |
| Manual pre-upgrade snapshot | Before each upgrade | Manual | Azure Blob |

### 8.2 Test Recovery (Run Monthly)

```bash
# On a separate test machine or the Azure VM when NOT in failover:

# 1. List snapshots
restic snapshots

# 2. Restore latest snapshot
restic restore latest --target /opt/centauri/restore-test

# 3. Spin up test stack against restored data
# Point compose.yaml volume to restore-test directory
# Verify ERPNext loads and data is intact
```

### 8.3 Point-in-Time Recovery via Binlog

If you need to recover data from before a specific event (e.g. accidental delete):

```bash
# 1. Restore from Restic snapshot just before the event
restic restore <snapshot-id> --target /opt/centauri/pitr-restore

# 2. Apply binlogs from that point to desired time
mysqlbinlog \
  --start-datetime="2026-06-26 10:00:00" \
  --stop-datetime="2026-06-26 11:30:00" \
  /var/lib/mysql/mysql-bin.* \
  | mariadb -uroot -p"$DB_PASSWORD"
```

---

## 9. Day-2 Operations

### 9.1 Common Bench Commands

All bench commands run inside the `backend` container:

```bash
alias bench-exec='docker compose -p centauri exec backend bench --site erp.centauri.io'

# Clear cache
bench-exec clear-cache

# Run migrations after upgrade
bench-exec migrate

# Rebuild assets
bench-exec build --app erpnext

# Console access
bench-exec console

# Check scheduled jobs
bench-exec scheduled-jobs list
```

### 9.2 Monitoring Stack (Optional Add-on)

For production API visibility, add Prometheus + Grafana. Create `overrides/compose.monitoring.yaml`:

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - /opt/centauri/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    networks:
      - default

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=<grafana-password>
    networks:
      - default
```

Key metrics to monitor:
- `frappe_http_requests_total` (via Frappe metrics endpoint)
- MariaDB `Threads_connected`, `Innodb_row_lock_waits`
- Redis `used_memory`, `connected_clients`
- Container CPU/memory via `cadvisor`

### 9.3 Log Access

```bash
# All services
docker compose -p centauri logs -f

# Specific service
docker compose -p centauri logs -f backend

# ERPNext application logs (inside container)
docker compose -p centauri exec backend \
  tail -f /home/frappe/frappe-bench/logs/web.log
```

### 9.4 Scale Workers for Heavy API Load

```bash
# Scale queue workers on the fly
docker compose -p centauri up -d --scale queue-short=3 --scale queue-long=2
```

---

## 10. Upgrade Path

### ERPNext Version Upgrade

**Always back up first.**

```bash
# 1. Pull new image
ERPNEXT_VERSION=v16.26.0  # update in .env
docker compose -p centauri pull

# 2. Run migrations (use migrator override)
docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.migrator.yaml \
  --env-file .env \
  -p centauri \
  run --rm migrator

# 3. Restart full stack
docker compose -p centauri up -d

# 4. Rebuild assets if needed
docker compose -p centauri exec backend \
  bench --site erp.centauri.io build
```

### Keeping Azure in Sync After Upgrade

After upgrading the local stack, update the `.env` on the Azure VM with the new `ERPNEXT_VERSION` and pull the new image so it's ready for the next failover:

```bash
ssh centauri@<azure-vm-ip> bash << 'EOF'
  cd /opt/centauri/frappe_docker
  sed -i 's/ERPNEXT_VERSION=.*/ERPNEXT_VERSION=v16.26.0/' .env
  docker compose pull
  echo "Azure VM image updated"
EOF
```

---

## Appendix A — Security Checklist

- [ ] Change all default passwords (`DB_PASSWORD`, `admin` user, Redis `AUTH`)
- [ ] ERPNext admin user is not `Administrator` — create a named user
- [ ] API users have minimum required permissions (role-based, not admin)
- [ ] API keys stored in Azure Key Vault or `pass`, not in `.env` files in git
- [ ] `.env` is in `.gitignore` — never commit it
- [ ] Traefik dashboard protected with `HASHED_PASSWORD` (see `example.env`)
- [ ] MariaDB port 3306 not exposed publicly — use SSH tunnel for replication
- [ ] Replication user `replicator` has only `REPLICATION SLAVE` privilege
- [ ] Restic password backed up separately from the repository it protects
- [ ] Failover scripts' Cloudflare API token scoped to DNS edit only (not full account)
- [ ] Azure VM uses SSH key auth only — disable password SSH

## Appendix B — Cost Estimate (Azure)

| Resource | SKU | Monthly Cost (Est.) |
|---|---|---|
| VM (Standard_B2s, deallocated) | ~0 hrs/month compute | ~$1 (disk only) |
| VM (Standard_B2s, running) | 8 hrs/day failover | ~$8 |
| Storage account (100 GB) | Standard LRS | ~$2 |
| Azure Container Instance (heartbeat) | 0.5 vCPU, 1 GB, always-on | ~$5 |
| Azure DNS zone | 1 zone | ~$1 |
| **Total (normal)** | | **~$9/month** |
| **Total (heavy failover, 8h/day)** | | **~$17/month** |

## Appendix C — Quick Reference Card

```
Start local stack:    systemctl start centauri-erpnext
Stop local stack:     systemctl stop centauri-erpnext
Status:               docker compose -p centauri ps
Logs:                 docker compose -p centauri logs -f
Bench shell:          docker compose -p centauri exec backend bash
Bench migrate:        docker compose -p centauri exec backend bench --site erp.centauri.io migrate
Force backup now:     /opt/centauri/scripts/backup.sh
Check replication:    docker compose -p centauri exec db mariadb -uroot -p$DB_PASSWORD -e "SHOW SLAVE STATUS\G"
Manual failover:      bash /scripts/failover.sh
Manual failback:      bash /scripts/failback.sh
Restic list:          RESTIC_REPOSITORY=azure:restic-repo:/ restic snapshots
```
