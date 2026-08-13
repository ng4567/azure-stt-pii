param location string
param capacityName string
param capacityAdministrator string
param skuName string

resource capacity 'Microsoft.Fabric/capacities@2025-01-15-preview' = {
  name: capacityName
  location: location
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
