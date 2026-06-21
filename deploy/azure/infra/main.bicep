// Cheapest viable production footprint for Frappe/ERPNext on Azure.
// Scope: subscription. Creates a resource group and all resources inside it.
//
// Cost-minimised SKUs (West Europe). Each has a documented upgrade path:
//   AKS        : Free control-plane tier + 1x Standard_B2s node (kubenet).
//   ACR        : Basic.
//   MySQL      : Flexible Server, Burstable Standard_B1ms, 32 GB.
//   Redis      : Azure Cache for Redis Basic C0 (250 MB).
//   Files      : provisioned in-cluster via the azurefile-csi (Standard) StorageClass.
//
// The shared `sites` volume MUST be ReadWriteMany -> Azure Files (handled by Helm
// PVC against the azurefile-csi storage class, not by this template).

targetScope = 'subscription'

@description('Deployment environment short name, used in resource names.')
param env string = 'prod'

@description('Azure region.')
param location string = 'westeurope'

@description('Resource group name.')
param rgName string = 'rg-centauri-erpnext-${env}'

@description('Number of AKS nodes. 1 = cheapest (no HA). Use >=2 for production HA.')
@minValue(1)
param aksNodeCount int = 1

@description('AKS node VM size. B2s = cheapest viable; B2ms (8GB) recommended if pods get evicted.')
param aksNodeVmSize string = 'Standard_B2s'

@description('MySQL administrator login.')
param mysqlAdminUser string = 'erpnextadmin'

@description('MySQL administrator password.')
@secure()
param mysqlAdminPassword string

@description('Object ID of the principal (you / a group) to grant Key Vault admin.')
param kvAdminObjectId string

var namePrefix = 'centauri-erpnext-${env}'
var acrName = replace('centerpnext${env}acr', '-', '') // ACR names: alphanumeric only

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
}

module resources 'resources.bicep' = {
  name: 'erpnext-resources'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    acrName: acrName
    aksNodeCount: aksNodeCount
    aksNodeVmSize: aksNodeVmSize
    mysqlAdminUser: mysqlAdminUser
    mysqlAdminPassword: mysqlAdminPassword
    kvAdminObjectId: kvAdminObjectId
  }
}

output resourceGroup string = rg.name
output acrLoginServer string = resources.outputs.acrLoginServer
output aksName string = resources.outputs.aksName
output keyVaultName string = resources.outputs.keyVaultName
output mysqlFqdn string = resources.outputs.mysqlFqdn
output redisHostname string = resources.outputs.redisHostname
