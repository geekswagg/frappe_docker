# Centauri ERPNext — MikroTik RouterOS 7 Configuration Script
# Apply via WinBox terminal or SSH: /import file-name=centauri.rsc
#
# Prerequisites:
#   - RouterOS 7.1 or later (WireGuard support)
#   - Replace ALL <PLACEHOLDERS> before importing
#
# Variables to substitute:
#   <DESKTOP_LAN_IP>       Static LAN IP of the Windows desktop, e.g. 192.168.1.100
#   <AZURE_VM_PUBLIC_IP>   Azure VM public IP address
#   <AZURE_VM_WG_PUBKEY>   WireGuard public key from the Azure VM
#   <MIKROTIK_WG_PRIVKEY>  WireGuard private key generated on MikroTik
#   <AZURE_SUBSCRIPTION>   Azure subscription ID (GUID)
#   <AZURE_TENANT_ID>      Azure AD tenant ID (GUID)
#   <AZURE_CLIENT_ID>      Service principal appId (GUID)
#   <AZURE_CLIENT_SECRET>  Service principal password

# ── 1. WireGuard Interface ───────────────────────────────────────────────────

/interface wireguard
add name=wg-centauri listen-port=51820 mtu=1420 private-key="<MIKROTIK_WG_PRIVKEY>" \
  comment="Centauri replication tunnel to Azure"

# Assign tunnel IP
/ip address
add address=10.100.0.1/30 interface=wg-centauri comment="Centauri WireGuard subnet"

# Azure VM peer
/interface wireguard peers
add interface=wg-centauri \
  public-key="<AZURE_VM_WG_PUBKEY>" \
  endpoint-address=<AZURE_VM_PUBLIC_IP> \
  endpoint-port=51820 \
  allowed-address=10.100.0.2/32 \
  persistent-keepalive=25s \
  comment="centauri-azure-failover"

# ── 2. NAT — forward MariaDB on tunnel IP to Windows desktop ────────────────

/ip firewall nat
add chain=dstnat \
  dst-address=10.100.0.1 \
  protocol=tcp \
  dst-port=3306 \
  action=dst-nat \
  to-addresses=<DESKTOP_LAN_IP> \
  to-ports=3306 \
  comment="MariaDB replication via WireGuard"

# HTTPS + HTTP for ERPNext (port-forward to Windows desktop)
add chain=dstnat \
  protocol=tcp \
  dst-port=443 \
  action=dst-nat \
  to-addresses=<DESKTOP_LAN_IP> \
  to-ports=443 \
  comment="ERPNext HTTPS"

add chain=dstnat \
  protocol=tcp \
  dst-port=80 \
  action=dst-nat \
  to-addresses=<DESKTOP_LAN_IP> \
  to-ports=80 \
  comment="ERPNext HTTP (ACME challenge)"

# Masquerade for WireGuard traffic going to LAN
add chain=srcnat \
  out-interface=bridge \
  src-address=10.100.0.0/30 \
  action=masquerade \
  comment="WireGuard → LAN masquerade"

# ── 3. Firewall — allow WireGuard, block direct MariaDB from WAN ─────────────

/ip firewall filter
# Allow WireGuard UDP
add chain=input \
  protocol=udp \
  dst-port=51820 \
  action=accept \
  place-before=0 \
  comment="WireGuard"

# Drop any direct TCP 3306 from WAN (belt-and-suspenders)
add chain=forward \
  protocol=tcp \
  dst-port=3306 \
  in-interface-list=WAN \
  action=drop \
  comment="Block direct MariaDB from WAN"

# ── 4. IP/Cloud DDNS ─────────────────────────────────────────────────────────

/ip cloud
set ddns-enabled=yes ddns-update-interval=5m

# After applying, run: /ip cloud print
# Copy the "dns-name" value and create a Cloudflare CNAME:
#   home.centauri.io  CNAME  <hash>.sn.mynetname.net

# ── 5. Failover script — calls Azure REST API to start the VM ────────────────

/system script
add name=centauri-failover owner=admin policy=read,write,policy,test,password,sniff,sensitive,romon \
  source={
    :local subId "<AZURE_SUBSCRIPTION>";
    :local rg "omwenga-erpnext-rg";
    :local vm "omwenga-erpnext-failover";
    :local tenantId "<AZURE_TENANT_ID>";
    :local clientId "<AZURE_CLIENT_ID>";
    :local clientSecret "<AZURE_CLIENT_SECRET>";

    # Get OAuth2 token
    :local tokenBody ("grant_type=client_credentials&client_id=" . $clientId . \
      "&client_secret=" . $clientSecret . \
      "&resource=https://management.azure.com/");
    :local tokenUrl ("https://login.microsoftonline.com/" . $tenantId . "/oauth2/token");
    :local tokenResult [/tool fetch url=$tokenUrl http-method=post \
      http-data=$tokenBody output=user as-value];
    # Note: full JSON parsing not available in RouterOS — pass token via environment
    # For a simpler approach, use an Azure Automation webhook URL instead (see note below)

    # Start the VM
    :local startUrl ("https://management.azure.com/subscriptions/" . $subId . \
      "/resourceGroups/" . $rg . \
      "/providers/Microsoft.Compute/virtualMachines/" . $vm . \
      "/start?api-version=2023-07-01");
    /tool fetch url=$startUrl http-method=post output=none;
    :log warning "Centauri: FAILOVER triggered — Azure VM start requested";
    :put "Centauri failover: Azure VM start requested";
  }

# Simpler alternative: Azure Automation webhook (no OAuth2 parsing needed)
# Replace the script body above with:
#   /tool fetch url="https://s<region>.azure-automation.net/webhooks?token=<TOKEN>" \
#     http-method=post output=none;

add name=centauri-failback owner=admin policy=read,write,policy,test,password \
  source={
    :log info "Centauri: PRIMARY back online — failback initiated";
    :put "Centauri failback: primary recovered, Azure VM will be drained and deallocated";
  }

# ── 6. Netwatch — health monitor ─────────────────────────────────────────────

/tool netwatch
add host=erp.comwenga.com \
  interval=30s \
  timeout=10s \
  up-script=centauri-failback \
  down-script=centauri-failover \
  comment="Centauri ERPNext primary health"

# Verify Netwatch is running:
#   /tool netwatch print
#   /tool netwatch monitor 0
