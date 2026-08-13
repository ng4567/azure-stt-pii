targetScope = 'subscription'

@description('Deployment region for the resource group and Fabric capacity.')
param location string = 'eastus2'

@description('Resource group that contains the customer-owned Fabric capacity and Oracle database.')
param resourceGroupName string

@description('Lowercase alphanumeric Fabric capacity resource name.')
param fabricCapacityName string

@description('Fabric capacity administrator UPN in the same Entra tenant as the subscription.')
param fabricCapacityAdministrator string

@description('Lowest paid Fabric SKU that supports data agents.')
@allowed([
  'F2'
])
param fabricSku string = 'F2'

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
}

module fabricCapacity 'fabric-capacity.bicep' = {
  name: 'fabricCapacity'
  scope: resourceGroup
  params: {
    capacityAdministrator: fabricCapacityAdministrator
    capacityName: fabricCapacityName
    location: location
    skuName: fabricSku
  }
}

output resourceGroupId string = resourceGroup.id
output fabricCapacityId string = fabricCapacity.outputs.capacityId
