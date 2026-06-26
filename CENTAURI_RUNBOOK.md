# Centauri ERPNext Adoption Runbook

**Stack:** ERPNext v16.25.0 · Frappe v16 · MariaDB 11.8 · Redis 8.6  
**Primary:** Windows Desktop (WSL2 + Docker Engine) behind MikroTik RouterOS 7  
**Failover:** Azure VM (hot-standby, deallocated when not needed)  
**Pattern:** API-heavy integrations with WireGuard-tunnelled replication and Netwatch-driven failover

> **Platform decision (updated):** The Windows Desktop replaces the Ubuntu laptop as the recommended
> primary node. A desktop has no sleep/hibernate risk, RouterOS 7 provides native WireGuard for the
> secure replication tunnel to Azure, and MikroTik Netwatch handles heartbeat monitoring locally —
> eliminating the need for an always-on Azure Container Instance. See [Appendix D](#appendix-d--platform-comparison) for the full trade-off analysis.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Phase 1 — Windows Desktop Setup (WSL2 + Docker Engine)](#3-phase-1--windows-desktop-setup-wsl2--docker-engine)
4. [Phase 1b — Ubuntu 26.04 LTS Setup (alternative primary)](#4-phase-1b--ubuntu-2604-lts-setup-alternative-primary)
5. [Phase 2 — MikroTik RouterOS 7 Integration](#5-phase-2--mikrotik-routeros-7-integration)
6. [Phase 3 — Azure Failover Environment](#6-phase-3--azure-failover-environment)
7. [Phase 4 — Data Replication Strategy](#7-phase-4--data-replication-strategy)
8. [Phase 5 — Failover Automation](#8-phase-5--failover-automation)
9. [Phase 6 — API Configuration for Integrations](#9-phase-6--api-configuration-for-integrations)
10. [Phase 7 — Backup & Recovery](#10-phase-7--backup--recovery)
11. [Day-2 Operations](#11-day-2-operations)
12. [Upgrade Path](#12-upgrade-path)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CENTAURI ERPNEXT — HYBRID TOPOLOGY (v2: Windows Desktop + MikroTik)     │
│                                                                          │
│  ┌──────────────────────────────────┐    ┌───────────────────────────┐  │
│  │  PRIMARY — Windows Desktop        │    │  FAILOVER — Azure VM B2s  │  │
│  │  WSL2 + Docker Engine             │    │  Ubuntu 24.04             │  │
│  │                                   │    │                           │  │
│  │  ERPNext stack                    │    │  ERPNext stack (same)     │  │
│  │  (Gunicorn · Nginx · WS · RQ)     │    │                           │  │
│  │                                   │    │  ┌─────────────────────┐  │  │
│  │  MariaDB 11.8 (master) ═══════════╪════╪═▶│ MariaDB replica     │  │  │
│  │  Redis 8.6              WireGuard │    │  └─────────────────────┘  │  │
│  │                          tunnel   │    │  Redis 8.6                │  │
│  │  Restic ──────────────────────────╪────╪─▶ Azure Blob Storage      │  │
│  └─────────────┬────────────────────-┘    └──────────┬────────────────┘  │
│                │                                     │                   │
│  ┌─────────────▼───────────────────┐                │                   │
│  │  MikroTik RouterOS 7            │                │                   │
│  │                                 │                │                   │
│  │  • WireGuard peer ──────────────╪────────────────┘  (replication)   │
│  │  • Netwatch (health monitor) ───╪──→ triggers failover script        │
│  │  • IP/Cloud DDNS                │                                    │
│  │  • Port forward 443 → Desktop   │                                    │
│  │  • Firewall: WG-only for 3306   │                                    │
│  └─────────────────────────────────┘                                    │
│                                                                          │
│              ┌──────────────────────────────┐                           │
│              │  Cloudflare DNS              │                           │
│              │  erp.comwenga.com  TTL=60     │                           │
│              │  api.comwenga.com             │                           │
│              └──────────────────────────────┘                           │
└──────────────────────────────────────────────────────────────────────────┘

REPLICATION CHANNEL:  MikroTik WireGuard VPN  (10.100.0.1 ↔ 10.100.0.2)
                      MariaDB binlog travels inside encrypted tunnel
                      Port 3306 never exposed to the public internet

FAILOVER LOGIC:  MikroTik Netwatch pings /api/method/ping every 30s
  → 3 consecutive failures (~90s) → RouterOS script calls Azure REST API
  → Azure VM starts → containers come up → Cloudflare DNS updated
  → Desktop back online → Netwatch up-script → failback + VM deallocated
```

### Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Primary host | Windows Desktop (WSL2) | Always-on desktop, no sleep/hibernate risk |
| Docker runtime | WSL2 + Docker Engine (no Desktop) | Native Linux perf, no Docker Desktop license |
| Replication tunnel | MikroTik WireGuard | Encrypted, zero public port exposure for MariaDB |
| Heartbeat monitor | MikroTik Netwatch | Free, on-device, no extra Azure infra needed |
| DDNS | MikroTik IP/Cloud (free) | Automatic, no third-party service |
| Replication method | MariaDB semi-sync binlog + Restic | Near-real-time + full-backup safety net |
| DNS failover | Cloudflare API (TTL=60) | Sub-60s propagation |
| API auth | Frappe API keys + OAuth2 | Stateless, revocable, per-integration scope |
| Compose style | Base `compose.yaml` + layered overrides | Identical stack on Windows and Azure |

---

## 2. Prerequisites

### 2.1 Primary Machine (Windows Desktop)

- Windows 10 22H2+ or Windows 11 (WSL2 requires a current build)
- **Minimum:** 16 GB RAM, 6+ CPU cores, 200 GB SSD free for WSL2 + Docker volumes
- WSL2 feature enabled (see Phase 1 for one-command install)
- Static LAN IP assigned via MikroTik DHCP reservation
- MikroTik RouterOS 7.x (WireGuard requires ≥ 7.1)
- Domain with Cloudflare DNS (for API-based record updates during failover)

### 2.2 Azure

- Azure subscription (Pay-As-You-Go is fine)
- Resource group: `omwenga-erpnext-rg`
- Services provisioned in Phase 2:
  - VM: Standard B2s (2 vCPU, 4 GB RAM) — stopped by default to save cost
  - Storage account for Restic backups and binlog archives
  - Logic App for heartbeat + failover orchestration
  - Azure DNS zone or reliance on Cloudflare API

### 2.3 Tooling

On **Windows** (run in PowerShell as Administrator — covered in detail in Phase 1):
```powershell
wsl --install                          # enables WSL2 + installs Ubuntu
winget install Microsoft.AzureCLI      # Azure CLI for failover scripts
```

Inside the **WSL2 Ubuntu** instance:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
sudo apt-get install -y restic
```

On the **Azure VM** (Ubuntu 24.04 — covered in Phase 3):
```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker centauri
sudo apt-get install -y restic wireguard-tools
```

---

## 3. Phase 1 — Windows Desktop Setup (WSL2 + Docker Engine)

### 3.1 Enable WSL2 and Install Ubuntu

Run in **PowerShell (Administrator)**:

```powershell
# Install WSL2 with Ubuntu 24.04 (ships with all Windows 11 / Win10 22H2+)
wsl --install -d Ubuntu-24.04

# After reboot, set WSL2 as default
wsl --set-default-version 2
wsl --set-default Ubuntu-24.04
```

### 3.2 Tune WSL2 Resource Limits

Create `C:\Users\<YourUser>\.wslconfig` (replace `<YourUser>`):

```ini
[wsl2]
# Leave ~6 GB for Windows. ERPNext needs ~4-5 GB under normal load.
memory=10GB
processors=6
swap=4GB

# Store the WSL2 VHD on your fastest drive
# (leave commented to use default %LOCALAPPDATA%\Packages\...)
# kernel=

[experimental]
# Enable sparse VHD so the disk image only consumes space actually used
sparseVhd=true
```

Restart WSL2 after creating this file:
```powershell
wsl --shutdown
wsl
```

### 3.3 Install Docker Engine Inside WSL2

Open an **Ubuntu WSL2 terminal** (not Docker Desktop — no license required):

```bash
# Docker Engine — native Linux, no VM-within-VM overhead
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker run --rm hello-world
```

Configure Docker daemon for better WSL2 performance. Create `/etc/docker/daemon.json`:

```json
{
  "storage-driver": "overlay2",
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

**Important — keep volumes in the WSL2 filesystem:**
Docker volumes at `/var/lib/docker/volumes/` inside WSL2 have native Linux I/O speed.
Avoid mounting paths under `/mnt/c/` or `/mnt/d/` for database volumes — cross-filesystem
I/O is 5-10× slower and will make MariaDB noticeably sluggish.

### 3.4 Auto-start Docker on Windows Boot

WSL2 does not start automatically on login by default. Create a Windows Task Scheduler task
that launches the WSL2 instance (and with it, the Docker daemon):

1. Open **Task Scheduler** → Create Task
2. **General:** Name = `Centauri WSL2 Docker`, Run whether user is logged in or not, Run with highest privileges
3. **Triggers:** At startup, delay 30 seconds
4. **Actions:** Start a program
   - Program: `wsl.exe`
   - Arguments: `-u root service docker start`
5. **Conditions:** Uncheck "Start only if on AC power"

Or do this via PowerShell (run as Administrator, once):

```powershell
$action  = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-u root service docker start"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "Centauri WSL2 Docker" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
```

### 3.5 Prevent Unplanned Reboots from Windows Update

```powershell
# Set Active Hours (Windows won't reboot during these hours for updates)
# Adjust to your working hours — this example is 07:00–23:00
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" `
  -Name "ActiveHoursStart" -Value 7
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" `
  -Name "ActiveHoursEnd" -Value 23

# Defer feature updates 365 days, quality updates 14 days (GPO equivalent via registry)
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" `
  -Name "DeferFeatureUpdates" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" `
  -Name "DeferFeatureUpdatesPeriodInDays" -Value 365 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" `
  -Name "DeferQualityUpdatesPeriodInDays" -Value 14 -Force
```

### 3.6 Clone and Configure ERPNext

Inside the **WSL2 Ubuntu terminal**:

```bash
sudo mkdir -p /opt/centauri
sudo chown $USER:$USER /opt/centauri

git clone https://github.com/geekswagg/frappe_docker.git /opt/centauri/frappe_docker
cd /opt/centauri/frappe_docker

cp centauri/config/centauri.env.template .env
# Edit .env — fill in DB_PASSWORD, LETSENCRYPT_EMAIL, SITES_RULE
nano .env
```

### 3.7 Launch the Stack

```bash
cd /opt/centauri/frappe_docker

docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.https.yaml \
  -f overrides/compose.backup-cron.yaml \
  -f overrides/compose.mariadb-replication.yaml \
  --env-file .env \
  -p centauri \
  up -d
```

### 3.8 Create the ERPNext Site

```bash
# Wait for configurator to finish (~2 min on first boot)
docker compose -p centauri logs -f configurator

# Create site
docker compose -p centauri exec backend \
  bench new-site erp.comwenga.com \
    --db-root-password "$(grep DB_PASSWORD /opt/centauri/frappe_docker/.env | cut -d= -f2)" \
    --admin-password "<admin-password>" \
    --install-app erpnext

docker compose -p centauri exec backend \
  bench --site erp.comwenga.com enable-scheduler
```

### 3.9 WSL2 Auto-start for the ERPNext Stack

Add to `/etc/rc.local` inside WSL2 (created if missing):

```bash
sudo tee /etc/rc.local > /dev/null << 'EOF'
#!/bin/bash
cd /opt/centauri/frappe_docker
docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.https.yaml \
  -f overrides/compose.backup-cron.yaml \
  -f overrides/compose.mariadb-replication.yaml \
  --env-file .env \
  -p centauri \
  up -d
exit 0
EOF
sudo chmod +x /etc/rc.local
```

Update the Task Scheduler action (step 3.4) to also run rc.local:
```powershell
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-u root bash /etc/rc.local"
```

### 3.10 Create Replication User

```bash
DB_PASSWORD="$(grep DB_PASSWORD /opt/centauri/frappe_docker/.env | cut -d= -f2)"

docker compose -p centauri exec db \
  mariadb -uroot -p"$DB_PASSWORD" -e "
    CREATE USER IF NOT EXISTS 'replicator'@'10.100.0.%'
      IDENTIFIED BY '<replication-password>';
    GRANT REPLICATION SLAVE ON *.* TO 'replicator'@'10.100.0.%';
    FLUSH PRIVILEGES;
  "
```

The `10.100.0.%` scope restricts the replication user to the WireGuard tunnel subnet only.

---

## 4. Phase 1b — Ubuntu 26.04 LTS Setup (alternative primary)

> Skip this section if using the Windows Desktop as primary (recommended).
> Keep it as a reference if you prefer a Linux-native host or want a second local node.

### 4.1 Clone and Configure

```bash
git clone https://github.com/geekswagg/frappe_docker.git /opt/centauri/frappe_docker
cd /opt/centauri/frappe_docker
cp centauri/config/centauri.env.template .env
nano .env
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
SITES_RULE=Host(`erp.comwenga.com`)
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
  bench new-site erp.comwenga.com \
    --db-root-password "$DB_PASSWORD" \
    --admin-password "<admin-password>" \
    --install-app erpnext

# Enable scheduler
docker compose -p centauri exec backend \
  bench --site erp.comwenga.com enable-scheduler
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

## 5. Phase 2 — MikroTik RouterOS 7 Integration

MikroTik handles three jobs that would otherwise require separate cloud services:
1. **WireGuard VPN** — secure tunnel for MariaDB replication to Azure (no public port needed)
2. **Netwatch** — local heartbeat monitor that fires failover/failback scripts
3. **IP/Cloud DDNS** — keeps a stable hostname even if your ISP changes your public IP

All configuration is done via MikroTik's terminal (`/terminal` in WinBox or SSH).

### 5.1 Enable IP/Cloud DDNS

```
/ip cloud set ddns-enabled=yes ddns-update-interval=5m
/ip cloud print
```

This gives you a hostname like `<hash>.sn.mynetname.net`. Set a CNAME in Cloudflare:
`home.comwenga.com CNAME <hash>.sn.mynetname.net` — or update your `PRIMARY_URL` env var to use this directly.

### 5.2 Port Forward HTTPS to the Windows Desktop

Replace `192.168.1.100` with the static LAN IP you reserved for the Windows desktop:

```
/ip firewall nat add \
  chain=dstnat \
  protocol=tcp \
  dst-port=443 \
  action=dst-nat \
  to-addresses=192.168.1.100 \
  to-ports=443 \
  comment="ERPNext HTTPS"

/ip firewall nat add \
  chain=dstnat \
  protocol=tcp \
  dst-port=80 \
  action=dst-nat \
  to-addresses=192.168.1.100 \
  to-ports=80 \
  comment="ERPNext HTTP (ACME challenge)"
```

### 5.3 Generate WireGuard Keys

On both the MikroTik **and** the Azure VM, generate key pairs:

**On MikroTik:**
```
/interface wireguard add name=wg-centauri listen-port=51820 mtu=1420
/interface wireguard print           # note the public-key
```

**On Azure VM (Ubuntu):**
```bash
wg genkey | tee /etc/wireguard/azure-private.key | wg pubkey > /etc/wireguard/azure-public.key
cat /etc/wireguard/azure-public.key   # copy this
```

### 5.4 Configure WireGuard on MikroTik

```
# Add Azure VM as a peer (replace with actual Azure VM public IP and public key)
/interface wireguard peers add \
  interface=wg-centauri \
  public-key="<AZURE_VM_WG_PUBLIC_KEY>" \
  endpoint-address=<AZURE_VM_PUBLIC_IP> \
  endpoint-port=51820 \
  allowed-address=10.100.0.2/32 \
  persistent-keepalive=25s \
  comment="centauri-azure-failover"

# Assign WireGuard interface an IP in the tunnel subnet
/ip address add address=10.100.0.1/30 interface=wg-centauri
```

### 5.5 Configure WireGuard on Azure VM

Create `/etc/wireguard/wg0.conf` on the Azure VM:

```ini
[Interface]
PrivateKey = <contents of /etc/wireguard/azure-private.key>
Address    = 10.100.0.2/30
ListenPort = 51820

[Peer]
# MikroTik router
PublicKey           = <MIKROTIK_WG_PUBLIC_KEY>
Endpoint            = <MIKROTIK_PUBLIC_IP_OR_CLOUD_DNS>:51820
AllowedIPs          = 10.100.0.1/32, 192.168.1.100/32
PersistentKeepalive = 25
```

```bash
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0

# Verify tunnel
ping 10.100.0.1
```

The WireGuard interface on MikroTik must be reachable from Azure. Open port 51820/UDP on the
MikroTik firewall:

```
/ip firewall filter add \
  chain=input \
  protocol=udp \
  dst-port=51820 \
  action=accept \
  comment="WireGuard"
```

### 5.6 Route MariaDB Replication Through the Tunnel

On the Windows desktop (WSL2), add a static route so traffic to `10.100.0.2` goes via the
tunnel (MikroTik handles NAT transparently since the desktop is behind it):

```bash
# Inside WSL2 — the gateway is the MikroTik LAN IP
sudo ip route add 10.100.0.0/30 via 192.168.1.1
```

The Azure VM replica will connect to the master at `10.100.0.1` (MikroTik WireGuard IP,
which NATs to `192.168.1.100:3306` — the Windows desktop MariaDB).

> Alternatively, configure MikroTik NAT to forward `10.100.0.1:3306 → 192.168.1.100:3306`:
> ```
> /ip firewall nat add chain=dstnat dst-address=10.100.0.1 protocol=tcp \
>   dst-port=3306 action=dst-nat to-addresses=192.168.1.100 to-ports=3306
> ```

### 5.7 MikroTik Netwatch — Health Monitor and Failover Trigger

Netwatch polls a URL every N seconds and runs RouterOS scripts on state change.

First, create the failover notification scripts. These call the Azure REST API to start the VM:

```
/system script add name=centauri-failover source={
  :local subId "<YOUR_AZURE_SUBSCRIPTION_ID>";
  :local rg "omwenga-erpnext-rg";
  :local vm "omwenga-erpnext-failover";
  :local token [/tool fetch url="https://login.microsoftonline.com/<TENANT_ID>/oauth2/token" \
    http-method=post \
    http-data="grant_type=client_credentials&client_id=<CLIENT_ID>&client_secret=<CLIENT_SECRET>&resource=https://management.azure.com/" \
    output=user as-value];
  :local startUrl ("https://management.azure.com/subscriptions/" . $subId . \
    "/resourceGroups/" . $rg . "/providers/Microsoft.Compute/virtualMachines/" . $vm . \
    "/start?api-version=2023-07-01");
  /tool fetch url=$startUrl http-method=post \
    http-header-field=("Authorization: Bearer " . ($token->"data")) \
    output=none;
  :log warning "Centauri: failover triggered — Azure VM starting";
}

/system script add name=centauri-failback source={
  :log info "Centauri: primary back online — failback will run automatically via heartbeat.sh";
}
```

Add the Netwatch entry:

```
/tool netwatch add \
  host=erp.comwenga.com \
  interval=30s \
  timeout=10s \
  up-script=centauri-failback \
  down-script=centauri-failover \
  comment="Centauri ERPNext health"
```

> **Service Principal for Netwatch:** The Azure REST API calls need a service principal with
> `Contributor` role scoped to the VM resource only. Create it with:
> ```bash
> az ad sp create-for-rbac \
>   --name centauri-netwatch \
>   --role Contributor \
>   --scopes /subscriptions/<SUB>/resourceGroups/omwenga-erpnext-rg/providers/Microsoft.Compute/virtualMachines/omwenga-erpnext-failover
> ```
> Copy the `appId` (CLIENT_ID), `password` (CLIENT_SECRET), and `tenant` (TENANT_ID) into the script above.

### 5.8 Firewall — Restrict MariaDB Port

MariaDB must only be reachable from the WireGuard tunnel subnet, never the public internet:

```
# On the Windows machine (WSL2) — block 3306 from everything except the WireGuard interface
# This is enforced inside WSL2 via iptables:
sudo iptables -A INPUT -p tcp --dport 3306 -s 10.100.0.0/30 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 3306 -j DROP

# Persist iptables rules
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

---

## 6. Phase 3 — Azure Failover Environment

### 6.1 Provision Azure Resources

```bash
az login
RESOURCE_GROUP="omwenga-erpnext-rg"
LOCATION="eastus"    # pick the region closest to you
VM_NAME="omwenga-erpnext-failover"

# Resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# Storage account for Restic + binlog archives
az storage account create \
  --name omwengabackups \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2

# Blob container for Restic repo
az storage container create \
  --name restic-repo \
  --account-name omwengabackups

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
  --resource-group omwenga-erpnext-rg \
  --name omwenga-erpnext-failover
```

The VM is now in a stopped (deallocated) state — no compute cost until failover triggers it.

---

## 7. Phase 4 — Data Replication Strategy

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
export AZURE_ACCOUNT_NAME="omwengabackups"
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
SITE="erp.comwenga.com"

export RESTIC_REPOSITORY="azure:restic-repo:/"
export AZURE_ACCOUNT_NAME="omwengabackups"
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

## 8. Phase 5 — Failover Automation

With the Windows Desktop + MikroTik setup, **MikroTik Netwatch is the primary trigger** (configured in Phase 2, step 5.7). The scripts in `centauri/scripts/` are invoked by Netwatch via SSH or the Azure REST API call embedded in the RouterOS script. This section documents the full flow and the scripts for reference.

### 8.1 Failover Flow

```
MikroTik Netwatch → 3 failed pings (90s total)
  → RouterOS script: Azure REST API → vm start
  → failover.sh (SSH from Netwatch host or Azure Automation):
      1. Wait for VM running
      2. Check replication lag
      3. Promote replica OR restore from Restic
      4. Start full ERPNext stack
      5. Update Cloudflare DNS → Azure IP
```

### 8.2 Failover Script (`failover.sh`)

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
  curl -s "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=erp.comwenga.com" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result[0].id'
)" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"erp.comwenga.com\",\"content\":\"${AZURE_VM_PUBLIC_IP}\",\"ttl\":60,\"proxied\":false}"

echo "=== FAILOVER COMPLETE: $(date -Iseconds) === DNS → Azure"
```

### 8.3 Failback Script (`failback.sh`)

```bash
#!/bin/bash
set -euo pipefail
echo "=== FAILBACK INITIATED: $(date -Iseconds) ==="

# 1. Wait for laptop ERPNext to be fully healthy
sleep 120

# 2. Update DNS back to laptop
LAPTOP_IP=$(curl -s https://api.ipify.org)  # or use your DDNS hostname
curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/$(
  curl -s "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?name=erp.comwenga.com" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result[0].id'
)" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"erp.comwenga.com\",\"content\":\"${LAPTOP_IP}\",\"ttl\":60,\"proxied\":false}"

# 3. Stop Azure VM (save cost)
# Allow 5 min for any in-flight requests to drain
sleep 300

ssh centauri@"$AZURE_VM_IP" bash << 'REMOTE_EOF'
  cd /opt/centauri/frappe_docker
  docker compose -p centauri stop backend frontend websocket queue-short queue-long scheduler
  echo "ERPNext stack stopped on Azure"
REMOTE_EOF

az vm deallocate \
  --resource-group omwenga-erpnext-rg \
  --name omwenga-erpnext-failover

echo "=== FAILBACK COMPLETE: $(date -Iseconds) === DNS → Desktop"
```

### 8.4 Pre-configure DNS TTL

Before go-live, set the DNS record TTL to 60 seconds so failover DNS changes propagate quickly.
Set this at least 24 hours before the first expected switchover so resolvers expire the old long-TTL cached record.

---

## 9. Phase 6 — Omwenga Holdings Group Company Setup

This phase must be completed **before** entering any operational data. Company structure in
ERPNext is largely irreversible once transactions exist against it.

### 9.1 Site Creation with Group Context

When creating the site (Phase 1, step 3.8), use `erp.comwenga.com` as the site name:

```bash
docker compose -p centauri exec backend \
  bench new-site erp.comwenga.com \
    --db-root-password "$(grep DB_PASSWORD /opt/centauri/frappe_docker/.env | cut -d= -f2)" \
    --admin-password "<admin-password>" \
    --install-app erpnext
```

### 9.2 Create the Group Company Structure

Log in as Administrator at `https://erp.comwenga.com`, then go to:
**Setup → Company → New**

Create companies in this exact order (parent must exist before children):

| Step | Company Name | Abbreviation | Is Group | Parent Company | Default Currency |
|---|---|---|---|---|---|
| 1 | Omwenga Holdings | OHL | ✅ Yes | *(none)* | KES |
| 2 | Centauri Consulting | CC | ☐ No | Omwenga Holdings | KES |
| 3 | Giktek | GKT | ☐ No | Omwenga Holdings | KES |
| 4 | Techno Brain Incubator | TBI | ☐ No | Omwenga Holdings | KES |

> **Abbreviation matters** — it prefixes all auto-generated account codes and document series.
> Keep it short (2-3 chars) and unique. You cannot change it after transactions exist.

### 9.3 Chart of Accounts

ERPNext will create a default chart of accounts for each company from the Kenya template
(select **Kenya - Chart of Accounts** during company creation).

For a holding structure, configure **intercompany accounts** so cost allocations and loans
between subsidiaries are tracked:

In each subsidiary's Chart of Accounts, add:
- `Intercompany Receivable` under Current Assets
- `Intercompany Payable` under Current Liabilities

Link these in **Accounts Settings → Intercompany Account** for each company pair.

### 9.4 Consolidated Reporting

Once all companies are set up, ERPNext's built-in consolidated reports work automatically:

- **Consolidated Balance Sheet:** Accounting → Reports → Consolidated Balance Sheet
  → Select parent: Omwenga Holdings
- **Consolidated Profit & Loss:** Same path → Consolidated Profit and Loss Statement
- **Group Cash Flow:** Accounting → Reports → Cash Flow (filter by group)

These reports eliminate intercompany transactions and present the group as a single entity.

### 9.5 User Access per Company

Each user should be scoped to the companies they work in:

**Setup → Users → [user] → Allow Modules + Company Access:**

| Role example | Company access |
|---|---|
| Centauri Finance Manager | Centauri Consulting only |
| Group CFO | All four companies |
| Giktek Operations | Giktek only |
| Group Administrator | All four companies |

Roles are company-agnostic in ERPNext but document visibility is filtered by the user's
allowed companies.

### 9.6 DNS Records to Create in Cloudflare

For the `comwenga.com` zone, create these records before going live:

| Type | Name | Value | TTL | Notes |
|---|---|---|---|---|
| A | `erp` | `<your public IP>` | 60 | Main ERP URL — updated by failover scripts |
| A | `api` | `<your public IP>` | 60 | Alias for integration docs; same target |
| CNAME | `home` | `<hash>.sn.mynetname.net` | 3600 | MikroTik IP/Cloud DDNS |

Set TTL to **60 seconds** on the `erp` and `api` records now, even before go-live, so
Cloudflare's resolvers cache the short TTL. The MikroTik Netwatch failover script updates
these `A` records automatically.

### 9.7 Email Configuration per Company

Each subsidiary will send email under its own domain. In ERPNext:

**Settings → Email Account → New** — create one outbound account per company:

| Company | Email | From Name |
|---|---|---|
| Omwenga Holdings | noreply@comwenga.com | Omwenga Holdings |
| Centauri Consulting | noreply@centauriconsulting.com | Centauri Consulting |
| Giktek | noreply@giktek.com | Giktek |
| Techno Brain Incubator | noreply@tecnobrain.com | Techno Brain Incubator |

Set each account's **Default Company** so outbound emails automatically use the correct
sender when documents are sent from that company.

---

## 10. Phase 7 — API Configuration for Integrations

ERPNext v16 exposes a comprehensive REST API. Here is how to configure it for the
Omwenga Holdings group's API-heavy integration pattern.

### 7.1 Enable API Access

```bash
# Increase API rate limits (run inside backend container)
docker compose -p centauri exec backend \
  bench --site erp.comwenga.com set-config -g \
  allow_cors_origin "https://your-integration-domain.com"

# Allow all origins for internal integrations (restrict in production)
docker compose -p centauri exec backend \
  bench --site erp.comwenga.com set-config -g \
  allow_cors "*"
```

### 7.2 Create API Key per Integration

In ERPNext UI → **Settings → Users** → select user → **API Access** section → Generate Keys.

Or via bench:

```bash
docker compose -p centauri exec backend \
  bench --site erp.comwenga.com execute frappe.core.doctype.user.user.generate_keys \
  --kwargs '{"user": "centauri-integration@technobraingbs.com"}'
```

Store keys in a secrets manager (Azure Key Vault or `pass`):

```bash
# Every integration should have its own ERPNext user with minimal permissions
# Example users:
#   api-crm@comwenga.com       → CRM read/write only
#   api-finance@comwenga.com   → Accounts read only
#   api-warehouse@comwenga.com → Stock read/write
```

### 7.3 Authentication — Two Patterns

**Pattern A: Token-based (preferred for server-to-server)**

```http
GET /api/resource/Sales Order HTTP/1.1
Host: erp.comwenga.com
Authorization: token <api_key>:<api_secret>
```

**Pattern B: OAuth2 (preferred for user-facing apps)**

```http
POST /api/method/frappe.integrations.oauth2.get_token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=user@comwenga.com&password=xxx&client_id=xxx&client_secret=xxx
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
- **Request URL:** `https://integrations.comwenga.com/webhooks/erpnext`
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
      - "--providers.docker.defaultRule=Host(`erp.comwenga.com`)"
    labels:
      - "traefik.http.middlewares.api-ratelimit.ratelimit.average=100"
      - "traefik.http.middlewares.api-ratelimit.ratelimit.burst=50"
      - "traefik.http.routers.erpnext.middlewares=api-ratelimit"
```

---

## 11. Phase 8 — Backup & Recovery

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

## 12. Day-2 Operations

### 9.1 Common Bench Commands

All bench commands run inside the `backend` container:

```bash
alias bench-exec='docker compose -p centauri exec backend bench --site erp.comwenga.com'

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

## 13. Upgrade Path

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
  bench --site erp.comwenga.com build
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

MikroTik Netwatch replaces the Azure Container Instance heartbeat monitor — saving ~$5/month.

| Resource | SKU | Monthly Cost (Est.) |
|---|---|---|
| VM (Standard_B2s, deallocated) | ~0 hrs/month compute | ~$1 (disk only) |
| VM (Standard_B2s, running) | 8 hrs/day failover | ~$8 |
| Storage account (100 GB) | Standard LRS | ~$2 |
| Azure DNS zone | 1 zone | ~$1 |
| ~~Azure Container Instance~~ | ~~replaced by MikroTik Netwatch~~ | ~~$0~~ |
| **Total (normal — no failover)** | | **~$4/month** |
| **Total (heavy failover, 8h/day)** | | **~$12/month** |

## Appendix C — Quick Reference Card

```
# ── Stack management (run in WSL2) ───────────────────────────────────────────
Start stack:          docker compose -p centauri up -d
Stop stack:           docker compose -p centauri down
Status:               docker compose -p centauri ps
Logs (all):           docker compose -p centauri logs -f
Logs (backend):       docker compose -p centauri logs -f backend

# ── Bench shortcuts ───────────────────────────────────────────────────────────
Bench shell:          docker compose -p centauri exec backend bash
Bench migrate:        docker compose -p centauri exec backend bench --site erp.comwenga.com migrate
Clear cache:          docker compose -p centauri exec backend bench --site erp.comwenga.com clear-cache
Build assets:         docker compose -p centauri exec backend bench --site erp.comwenga.com build

# ── Data & replication ────────────────────────────────────────────────────────
Force backup now:     bash /opt/centauri/frappe_docker/centauri/scripts/backup.sh
Check replication:    docker compose -p centauri exec db mariadb -uroot -p$DB_PASSWORD -e "SHOW SLAVE STATUS\G"
List Restic snaps:    RESTIC_REPOSITORY=azure:restic-repo:/ restic snapshots

# ── Failover / failback ───────────────────────────────────────────────────────
Manual failover:      bash /opt/centauri/frappe_docker/centauri/scripts/failover.sh
Manual failback:      bash /opt/centauri/frappe_docker/centauri/scripts/failback.sh

# ── MikroTik (WinBox terminal or SSH) ────────────────────────────────────────
Check Netwatch:       /tool netwatch print
WireGuard status:     /interface wireguard peers print
Check DDNS:           /ip cloud print
Run failover script:  /system script run centauri-failover

# ── WSL2 (PowerShell on Windows) ─────────────────────────────────────────────
Restart WSL2:         wsl --shutdown && wsl
WSL2 memory usage:    wsl -- free -h
Start Docker in WSL2: wsl -u root service docker start
```

## Appendix D — Platform Comparison

| Criterion | Windows Desktop (WSL2) | Ubuntu Laptop | Ubuntu Desktop |
|---|---|---|---|
| **Uptime risk** | Low — desktop, always-on | High — sleep/hibernate/battery | Low — always-on |
| **Docker overhead** | Minimal — Docker Engine in WSL2 | Native | Native |
| **Docker licensing** | None (Engine, not Desktop) | None | None |
| **WireGuard (replication)** | Via MikroTik router | Via MikroTik router | Via MikroTik router |
| **Heartbeat monitor** | MikroTik Netwatch (free) | MikroTik Netwatch (free) | MikroTik Netwatch (free) |
| **Auto-start** | Task Scheduler + WSL2 rc.local | systemd | systemd |
| **Windows Update reboot** | Manageable (Active Hours + deferral) | N/A | N/A |
| **RAM for ERPNext** | ~10 GB of 16 GB available to WSL2 | Depends on model | Depends on config |
| **File I/O for volumes** | Good (WSL2 ext4 VHD, not /mnt/c) | Native | Native |
| **Verdict** | **Recommended primary** | Acceptable secondary | Equal to Windows Desktop |

**Bottom line:** A Windows Desktop behind MikroTik RouterOS 7 is the recommended primary because
the desktop form factor eliminates the sleep/battery risk and the MikroTik provides WireGuard +
Netwatch for free — two cloud services you would otherwise pay for. WSL2 with Docker Engine
(not Docker Desktop) runs ERPNext at near-native Linux performance with no licensing concern.
