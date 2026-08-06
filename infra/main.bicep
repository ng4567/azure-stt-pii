targetScope = 'subscription'

@description('Azure region for the resource group, Foundry resource, and model deployment.')
param location string = 'eastus'

@description('Name of the resource group created for the Foundry resources.')
param resourceGroupName string = 'stt-pii'

@description('Globally unique name for the Microsoft Foundry resource.')
param foundryName string = 'stt-pii-${uniqueString(subscription().id)}'

@description('Name exposed to clients for the DeepSeek model deployment.')
param modelDeploymentName string = 'DeepSeek-V4-Flash'

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
}

module foundry 'foundry.bicep' = {
  name: 'foundry'
  scope: resourceGroup
  params: {
    location: location
    foundryName: foundryName
    modelDeploymentName: modelDeploymentName
  }
}

output resourceGroupName string = resourceGroup.name
output foundryName string = foundry.outputs.foundryName
output foundryEndpoint string = foundry.outputs.foundryEndpoint
output modelDeploymentName string = foundry.outputs.modelDeploymentName
