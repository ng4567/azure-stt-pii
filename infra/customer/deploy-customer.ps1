[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $FabricCapacityName,
  [Parameter(Mandatory)] [string] $FabricCapacityAdministrator,
  [string] $Location = 'eastus2'
)

$ErrorActionPreference = 'Stop'
$template = Join-Path $PSScriptRoot 'main.bicep'

az account set --subscription $SubscriptionId
az provider register --namespace Microsoft.Fabric | Out-Null
az provider register --namespace Oracle.Database | Out-Null

az deployment sub create `
  --name "charter-fabric-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))" `
  --location $Location `
  --template-file $template `
  --parameters `
    location=$Location `
    resourceGroupName=$ResourceGroupName `
    fabricCapacityName=$FabricCapacityName `
    fabricCapacityAdministrator=$FabricCapacityAdministrator

Write-Host 'Fabric capacity deployment submitted. Use deploy-oracle.ps1 after Oracle Database@Azure entitlement is active.'
