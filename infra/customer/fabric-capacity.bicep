@description('Region for the Fabric capacity.')
param location string

@description('Fabric capacity name; lowercase letters and digits only.')
param capacityName string

@description('Capacity administrator UPN or object ID from the subscription tenant.')
param capacityAdministrator string

@description('Fabric SKU name, for example F2.')
param skuName string

@description('Resource tags.')
param tags object = {}

// API version 2023-11-01 is deliberate. The ARM template reference documents
// 2025-01-15-preview as "latest", but that version is not registered for this
// resource type in most subscriptions and fails with NoRegisteredProviderFound.
// 2023-11-01 is the newest generally available version.
resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: capacityName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: [
        capacityAdministrator
      ]
    }
  }
}

output capacityId string = capacity.id
