[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $DatabaseName,
  [Parameter(Mandatory)] [string] $CustomerContactEmail,
  [Parameter(Mandatory)] [securestring] $AdminPassword,
  [Parameter(Mandatory)] [string] $DbVersion,
  [Parameter(Mandatory)] [string[]] $WhitelistedIps,
  [string] $Location = 'eastus2'
)

$ErrorActionPreference = 'Stop'
$template = Join-Path $PSScriptRoot 'oracle-autonomous.bicep'
$plainPassword = [System.Net.NetworkCredential]::new('', $AdminPassword).Password

az account set --subscription $SubscriptionId
$providerState = az provider show --namespace Oracle.Database --query registrationState -o tsv
if ($providerState -ne 'Registered') {
  throw "Oracle.Database provider is $providerState. Register it and wait for Registered before deployment."
}

az deployment group create `
  --resource-group $ResourceGroupName `
  --template-file $template `
  --parameters `
    location=$Location `
    databaseName=$DatabaseName `
    customerContactEmail=$CustomerContactEmail `
    adminPassword=$plainPassword `
    dbVersion=$DbVersion `
    whitelistedIps=$WhitelistedIps

Write-Host 'Oracle database deployment submitted. Run oracle-schema.sql using Oracle SQLcl after the database reaches Succeeded.'
