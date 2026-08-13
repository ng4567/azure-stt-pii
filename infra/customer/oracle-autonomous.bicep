@description('Region where Oracle Database@Azure is enabled for the customer.')
param location string

@description('Autonomous Database name, maximum 30 characters.')
param databaseName string

@description('Notification contact required by Oracle Database@Azure.')
param customerContactEmail string

@secure()
@description('Initial ADMIN password. Pass this from a secure deployment parameter source.')
param adminPassword string

@description('Network CIDR ranges allowed to reach the database. Use customer egress ranges only.')
param whitelistedIps array

@description('Oracle Database version enabled in the customer Oracle subscription.')
param dbVersion string

@description('Minimum Autonomous Database storage allocation in GiB.')
@minValue(20)
param storageGiB int = 20

resource autonomousDatabase 'Oracle.Database/autonomousDatabases@2025-09-01' = {
  name: databaseName
  location: location
  properties: {
    adminPassword: adminPassword
    autonomousMaintenanceScheduleType: 'Regular'
    characterSet: 'AL32UTF8'
    computeCount: 1
    computeModel: 'ECPU'
    customerContacts: [
      {
        email: customerContactEmail
      }
    ]
    dataBaseType: 'Regular'
    dataStorageSizeInGbs: storageGiB
    dbVersion: dbVersion
    dbWorkload: 'OLTP'
    displayName: databaseName
    isAutoScalingEnabled: false
    isAutoScalingForStorageEnabled: false
    isMtlsConnectionRequired: true
    licenseModel: 'LicenseIncluded'
    ncharacterSet: 'AL16UTF16'
    openMode: 'ReadWrite'
    whitelistedIps: whitelistedIps
  }
}

output databaseId string = autonomousDatabase.id
