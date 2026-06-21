// Resource-group-scoped resources for the ERPNext stack.
targetScope = 'resourceGroup'

param location string
param namePrefix string
param acrName string
param aksNodeCount int
param aksNodeVmSize string
param mysqlAdminUser string
@secure()
param mysqlAdminPassword string
param kvAdminObjectId string

// ---------------------------------------------------------------------------
// Container Registry (Basic)
// ---------------------------------------------------------------------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

// ---------------------------------------------------------------------------
// Log Analytics (cheap, pay-per-GB) for Container Insights
// ---------------------------------------------------------------------------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-law'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------
// Key Vault (Standard, RBAC)
// ---------------------------------------------------------------------------
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${take(replace(namePrefix, '-', ''), 20)}kv'
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// Key Vault Administrator for the named principal
resource kvAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, kvAdminObjectId, 'kv-admin')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '00482a5a-887f-4fb3-b363-3b7fe8e74483') // Key Vault Administrator
    principalId: kvAdminObjectId
    principalType: 'User'
  }
}

// Store the MySQL password as a secret
resource mysqlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'mysql-admin-password'
  properties: {
    value: mysqlAdminPassword
  }
}

// ---------------------------------------------------------------------------
// MySQL Flexible Server (Burstable B1ms) + erpnext database (utf8mb4)
// ---------------------------------------------------------------------------
resource mysql 'Microsoft.DBforMySQL/flexibleServers@2023-12-30' = {
  name: '${namePrefix}-mysql'
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: mysqlAdminUser
    administratorLoginPassword: mysqlAdminPassword
    version: '8.0.21'
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' } // cheapest; enable ZoneRedundant for HA
  }
}

// Frappe requires these server-level params.
resource mysqlCharset 'Microsoft.DBforMySQL/flexibleServers/configurations@2023-12-30' = {
  parent: mysql
  name: 'character_set_server'
  properties: { value: 'utf8mb4', source: 'user-override' }
}
resource mysqlCollation 'Microsoft.DBforMySQL/flexibleServers/configurations@2023-12-30' = {
  parent: mysql
  name: 'collation_server'
  properties: { value: 'utf8mb4_unicode_ci', source: 'user-override' }
  dependsOn: [ mysqlCharset ]
}

resource erpnextDb 'Microsoft.DBforMySQL/flexibleServers/databases@2023-12-30' = {
  parent: mysql
  name: 'erpnext'
  properties: {
    charset: 'utf8mb4'
    collation: 'utf8mb4_unicode_ci'
  }
}

// Allow other Azure services (incl. AKS egress) to reach MySQL.
// NOTE: public access + firewall is the cheap baseline. Phase 4 hardening =
// VNet integration / private endpoint.
resource mysqlAllowAzure 'Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30' = {
  parent: mysql
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ---------------------------------------------------------------------------
// Azure Cache for Redis (Basic C0). Single instance; Frappe uses db 0 (cache)
// and db 1 (queue) on the same host.
// ---------------------------------------------------------------------------
resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: '${namePrefix}-redis'
  location: location
  properties: {
    sku: {
      name: 'Basic'
      family: 'C'
      capacity: 0
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

// ---------------------------------------------------------------------------
// AKS (Free tier control plane, single B-series node, kubenet)
// ---------------------------------------------------------------------------
resource aks 'Microsoft.ContainerService/managedClusters@2024-05-01' = {
  name: '${namePrefix}-aks'
  location: location
  identity: { type: 'SystemAssigned' }
  sku: {
    name: 'Base'
    tier: 'Free'
  }
  properties: {
    dnsPrefix: '${namePrefix}-aks'
    agentPoolProfiles: [
      {
        name: 'system'
        count: aksNodeCount
        vmSize: aksNodeVmSize
        mode: 'System'
        osType: 'Linux'
        osDiskSizeGB: 32
        type: 'VirtualMachineScaleSets'
      }
    ]
    networkProfile: {
      networkPlugin: 'kubenet'
      loadBalancerSku: 'standard'
    }
    addonProfiles: {
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: law.id
        }
      }
    }
  }
}

// Let AKS pull from ACR (AcrPull on the kubelet identity).
resource aksAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, aks.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
  }
}

output acrLoginServer string = acr.properties.loginServer
output aksName string = aks.name
output keyVaultName string = kv.name
output mysqlFqdn string = mysql.properties.fullyQualifiedDomainName
output redisHostname string = redis.properties.hostName
