<#
.SYNOPSIS
  Deploys the Fabric capacity that hosts the call-analytics workspace and Data Agent.

.DESCRIPTION
  Runs pre-flight checks for the two failure modes that are easy to hit and
  produce unhelpful errors from the Fabric resource provider:

    * A capacity name containing characters outside ^[a-z][a-z0-9]*$ fails with
      "Invalid chars in resource name" and no indication of the actual rule.
    * A capacity administrator from a different Entra tenant than the
      subscription fails with "All administrators must belong to the same
      tenant" or "All provided principals must be existing, user or service
      principals".

  Both are cheaper to catch here than in a failed deployment.

.EXAMPLE
  ./deploy-customer.ps1 -SubscriptionId <sub> -ResourceGroupName rg-call-analytics `
    -FabricCapacityName fabriccallanalyticsf2 -FabricCapacityAdministrator admin@contoso.com
#>
[CmdletBinding(SupportsShouldProcess)]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,

  [Parameter(Mandatory)]
  [ValidatePattern('^[a-z][a-z0-9]*$', ErrorMessage =
    'Fabric capacity names must start with a lowercase letter and contain only lowercase letters and digits. Hyphens and underscores are rejected by the resource provider.')]
  [ValidateLength(3, 63)]
  [string] $FabricCapacityName,

  [Parameter(Mandatory)] [string] $FabricCapacityAdministrator,

  [ValidateSet('F2', 'F4', 'F8', 'F16', 'F32', 'F64')]
  [string] $FabricSku = 'F2',

  [string] $Location = 'eastus2',

  [switch] $WhatIfDeployment
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$templatePath = Join-Path $PSScriptRoot 'main.bicep'
if (-not (Test-Path -LiteralPath $templatePath)) {
  throw "Template not found at $templatePath."
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  throw 'Azure CLI is required. Install it and run "az login" before deploying.'
}

Write-Host 'Selecting subscription...'
az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) { throw "Could not select subscription $SubscriptionId. Run 'az login' first." }

$subscriptionTenantId = (az account show --query tenantId -o tsv)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($subscriptionTenantId)) {
  throw 'Could not read the subscription tenant. Run "az login" and retry.'
}

# Pre-flight: the capacity administrator must resolve inside the subscription's
# tenant. Fabric performs this check server-side but reports it obscurely.
# Try user first, then service principal: the capacity administration list
# accepts either, and 'az ad user show' cannot resolve a service principal.
Write-Host "Verifying '$FabricCapacityAdministrator' exists in tenant $subscriptionTenantId..."
$resolvedAdmin = az ad user show --id $FabricCapacityAdministrator --query userPrincipalName -o tsv 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resolvedAdmin)) {
  $resolvedAdmin = az ad sp show --id $FabricCapacityAdministrator --query appDisplayName -o tsv 2>$null
}
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resolvedAdmin)) {
  throw @"
Could not resolve '$FabricCapacityAdministrator' as a user or service principal in
tenant $subscriptionTenantId.

The Fabric capacity administrator must be a user or service principal in the
same Entra tenant as the subscription. A capacity also cannot be assigned to a
Fabric workspace in a different tenant, so confirm the Azure subscription and
the Fabric workspace share a tenant before continuing.
"@
}
Write-Host "  resolved: $resolvedAdmin"

Write-Host 'Registering the Microsoft.Fabric resource provider...'
az provider register --namespace Microsoft.Fabric --wait
if ($LASTEXITCODE -ne 0) { throw 'Failed to register the Microsoft.Fabric resource provider.' }

$deploymentName = "fabric-call-analytics-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
$deploymentArgs = @(
  'deployment', 'sub', $(if ($WhatIfDeployment) { 'what-if' } else { 'create' }),
  '--name', $deploymentName,
  '--location', $Location,
  '--template-file', $templatePath,
  '--parameters',
  "location=$Location",
  "resourceGroupName=$ResourceGroupName",
  "fabricCapacityName=$FabricCapacityName",
  "fabricCapacityAdministrator=$FabricCapacityAdministrator",
  "fabricSku=$FabricSku"
)

if (-not $PSCmdlet.ShouldProcess("$ResourceGroupName/$FabricCapacityName", "Deploy Fabric $FabricSku capacity")) {
  return
}

Write-Host "Submitting deployment $deploymentName..."
az @deploymentArgs
if ($LASTEXITCODE -ne 0) { throw 'Deployment failed. Review the Azure CLI output above.' }

if ($WhatIfDeployment) { return }

Write-Host ''
Write-Host 'Fabric capacity deployed. Remaining steps are Fabric tenant artifacts and are not ARM resources:'
Write-Host '  1. Create the Fabric workspace and assign it to this capacity.'
Write-Host '  2. Run oracle-schema.sql on the Oracle source as a schema owner.'
Write-Host '  3. Configure Fabric Mirroring for Oracle through the on-premises data gateway.'
Write-Host '  4. Create the Data Agent, SELECT the mirrored table, and publish.'
Write-Host ''
Write-Host 'Capacity billing accrues while the capacity is active. Pause it between demos:'
Write-Host "  az resource invoke-action --action suspend --ids /subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Fabric/capacities/$FabricCapacityName"
Write-Host 'Resume before a session, or the Data Agent reports that it is unavailable.'
