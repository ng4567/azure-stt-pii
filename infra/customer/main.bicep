targetScope = 'subscription'

@description('Deployment region for the resource group and Fabric capacity.')
param location string = 'eastus2'

@description('Resource group that will contain the Fabric capacity.')
param resourceGroupName string

@description('Fabric capacity name. Must start with a letter and contain only lowercase letters and digits; the Fabric resource provider rejects hyphens.')
@minLength(3)
@maxLength(63)
param fabricCapacityName string

@description('Fabric capacity administrator UPN or object ID. Must belong to the same Entra tenant as the subscription, otherwise the resource provider rejects the deployment.')
param fabricCapacityAdministrator string

@description('Fabric SKU. F2 is the smallest that supports Data Agents, but its Spark allocation is very small; choose F4 or higher if the workload authors Lakehouse tables with Spark.')
@allowed([
  'F2'
  'F4'
  'F8'
  'F16'
  'F32'
  'F64'
])
param fabricSku string = 'F2'

@description('Tags applied to the resource group and capacity.')
param tags object = {}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module fabricCapacity 'fabric-capacity.bicep' = {
  name: 'fabricCapacity'
  scope: resourceGroup
  params: {
    capacityAdministrator: fabricCapacityAdministrator
    capacityName: fabricCapacityName
    location: location
    skuName: fabricSku
    tags: tags
  }
}

output resourceGroupId string = resourceGroup.id
output fabricCapacityId string = fabricCapacity.outputs.capacityId
output fabricCapacityName string = fabricCapacityName
