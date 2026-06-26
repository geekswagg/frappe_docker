# Azure deployment (Centauri ERPNext)

Deploys the `frappe_docker` (ERPNext) stack to Azure: **AKS + Frappe Helm chart**,
**Azure Database for MySQL Flexible Server**, **Azure Cache for Redis**, image built
in **ACR**. CI/CD on **Azure DevOps** (`comwenga/000`). Tracked under Epic #3647.

## Target

| | |
|---|---|
| Subscription | `4729e600-3b89-45e2-b3be-f5a272095df9` (Cloudextend.gauntlet.Production) |
| Tenant | Centauri `3ac00dca-78e9-47af-adf2-d841dd297416` |
| Region | West Europe |
| ADO service connection | `centauri-development-subscription` |

## Layout

```
deploy/azure/
  infra/
    main.bicep        # subscription-scope: RG + resources module
    resources.bicep   # ACR, Key Vault, MySQL, Redis, AKS, role assignments
  pipelines/
    infra.yml         # what-if + provision (Phase 0 + data tier)
../../azure-pipelines.yml   # image build -> ACR (Phase 1)
```

## Cost-minimised SKUs (and upgrade path)

| Resource | Cheapest (this template) | Upgrade for production |
|---|---|---|
| AKS control plane | Free tier | Standard tier (uptime SLA) |
| AKS nodes | 1 × Standard_B2s (kubenet) | ≥2 × Standard_B2ms for HA |
| ACR | Basic | Standard/Premium (geo-replication) |
| MySQL | Burstable B1ms, 32 GB | GP D2ds + Zone-redundant HA |
| Redis | Basic C0 (250 MB) | Standard C1 (replication) |
| MySQL/Redis access | Public + firewall | VNet integration / private endpoint |
| `sites` volume | azurefile-csi Standard (RWX) | Premium Files |

> ⚠️ The defaults are **cheapest, not HA**. Single AKS node + no MySQL/Redis
> replication means downtime on node/instance failure. Bump `aksNodeCount` and the
> SKUs above before calling it production-grade.

## One-time ADO setup (before pipelines run)

1. **Variable group `erpnext-infra`** (Pipelines → Library):
   - `MYSQL_ADMIN_PASSWORD` (secret)
   - `KV_ADMIN_OBJECT_ID` = `3b2c80c3-e0ad-4456-a13f-3ba41d90cbd8` (Collins, Centauri tenant)
2. **Variable group `frappe-build`** (for the image pipeline): `ACR_NAME`, optional `APPS_JSON_BASE64`.
3. **Environment `erpnext-prod`** (Pipelines → Environments) — add an approval check to gate prod.
4. Register both YAML pipelines (New pipeline → Azure Repos Git → existing YAML).

## Order of operations

1. Run **`infra.yml`** → provisions RG/ACR/KeyVault/MySQL/Redis/AKS. Note the outputs
   (`acrLoginServer`, `aksName`, `mysqlFqdn`, `redisHostname`).
2. Set `ACR_NAME` in `frappe-build`, run **`azure-pipelines.yml`** → pushes the
   custom image to ACR.
3. **Phase 3 (next):** Helm-install the Frappe chart against the AKS cluster, pointing
   at the ACR image + managed MySQL/Redis, with an `azurefile-csi` PVC for `sites`,
   plus the one-time `bench new-site` job. (Helm values + deploy pipeline still to be authored.)
