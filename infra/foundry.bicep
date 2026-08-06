@description('Azure region for the Microsoft Foundry resource.')
param location string

@description('Globally unique name for the Microsoft Foundry resource.')
param foundryName string

@description('Name exposed to clients for the DeepSeek model deployment.')
param modelDeploymentName string

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: foundryName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource deepSeekFlash 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  name: modelDeploymentName
  parent: foundry
  sku: {
    name: 'GlobalStandard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'DeepSeek'
      name: 'DeepSeek-V4-Flash'
      version: '2026-04-23'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

output foundryName string = foundry.name
output foundryEndpoint string = foundry.properties.endpoint
output modelDeploymentName string = deepSeekFlash.name
