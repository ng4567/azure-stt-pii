# Customer deployment: Oracle to OneLake analytics

This folder provisions the Azure control-plane components for the call-transcript
analytics architecture:

```text
Architecture 2 service -> Oracle Autonomous Database -> Fabric Data Factory pipeline
-> OneLake Warehouse/Lakehouse -> Fabric Data Agent
```

## Prerequisites

1. An Azure subscription in the same Entra tenant as the Fabric workspace.
1. Permissions to create resource groups and `Microsoft.Fabric/capacities`.
1. An active Oracle Database@Azure Marketplace entitlement. This is customer-specific
   and cannot be inferred or accepted by a deployment script.
1. A supported Oracle Database@Azure region, Oracle database version, customer contact,
   and approved client-network CIDR ranges.
1. Fabric capacity administrator or workspace administrator rights.

## Deploy

```powershell
./deploy-customer.ps1 `
  -SubscriptionId <subscription-id> `
  -ResourceGroupName rg-call-analytics `
  -FabricCapacityName fabriccallanalyticsf2 `
  -FabricCapacityAdministrator <admin-upn>
```

The template deploys F2, the smallest paid Fabric SKU that supports Data Agents.
Pause the capacity outside active demo hours to control spend; resume it before using
the workspace.

After the Oracle Database@Azure entitlement is enabled, deploy the Autonomous
Database with `deploy-oracle.ps1`. Do not put the database password in source control,
shell history, or pipeline logs. Use a secure CI/CD secret or Key Vault-backed
parameter mechanism instead.

Run `oracle-schema.sql` through Oracle SQLcl or an approved database migration tool.
The table intentionally stores only a PII-safe transcript summary and normalized
analytics fields. Keep raw audio and unredacted transcripts in a separately governed
store, if retention is required.

## Fabric ingestion

Create a Fabric Data Factory pipeline in the target workspace with:

1. An Oracle Database connection using the Autonomous Database mTLS wallet from an
   approved secret store.
1. A copy activity from `CALL_ANALYTICS` to a Fabric Warehouse table or Lakehouse
   Delta table.
1. Incremental watermarking on `INGESTION_UTC`.
1. A Data Agent data source scoped only to the curated destination table.

Fabric connection and pipeline items are tenant/workspace artifacts, not ARM resources.
They require the customer tenant's Fabric configuration and cannot be safely created
with a generic ARM template. The connection should use a Fabric connection credential
or gateway appropriate to the customer network; never embed the wallet or password in
this repository.
