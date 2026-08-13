# Customer deployment: Oracle to OneLake call analytics

Reproducible infrastructure for the call-transcript analytics architecture:

```text
call audio
  -> speech-to-text + PII redaction  (existing Architecture 2 service)
  -> Oracle Database                 (existing system of record)
  -> Fabric Mirroring                (near-real-time replication, no ETL)
  -> OneLake                         (Delta, queryable by every Fabric engine)
  -> Fabric Data Agent               (governed natural-language analytics)
```

Only PII-safe summaries and normalized attributes cross into the analytics
store. Raw audio and unredacted transcripts stay in whatever governed store
already holds them.

## Why Mirroring rather than a copy pipeline

[Fabric Mirroring for Oracle](https://learn.microsoft.com/fabric/mirroring/oracle)
replicates Oracle tables into OneLake continuously by reading redo through
LogMiner. It matters here for three reasons:

1. **It is hosting-agnostic.** Mirroring supports Oracle 10 and above on
   on-premises hardware, Azure VMs, OCI, Oracle Database@Azure, and Exadata. The
   architecture does not have to be re-planned when the exact production
   deployment model is confirmed.
2. **The replication compute is free.** Microsoft does not charge for the
   compute that replicates data into OneLake, nor for mirrored-table storage up
   to the capacity's included allowance. Cost is incurred when the data is
   queried. A scheduled copy pipeline consumes capacity units on every run.
3. **There is no ETL to maintain.** No watermark column, no incremental-load
   logic, no pipeline to debug when the schema changes.

A Data Factory copy pipeline remains a valid fallback if LogMiner or the
archivelog prerequisites cannot be enabled on the source database.

## Prerequisites

Azure and Fabric:

- An Azure subscription in the **same Entra tenant** as the Fabric workspace.
  Fabric rejects a capacity administrator from a different tenant, and a
  capacity cannot be assigned to a workspace across tenants.
- Rights to create resource groups and `Microsoft.Fabric/capacities`.
- Fabric capacity administrator or workspace administrator rights.

Oracle side, required by Mirroring:

- Oracle Database 19c or later (Mirroring supports 10+).
- `ARCHIVELOG` mode enabled. Changing this requires an instance restart.
- Database-level and table-level supplemental logging enabled.
- The database open in read-write mode; LogMiner does not work against a
  read-only standby.
- An [on-premises data gateway](https://learn.microsoft.com/fabric/mirroring/oracle-tutorial)
  reachable from the Oracle host, including for Oracle running on an Azure VM.
- A dedicated replication account with the documented least-privilege grants.
  Do not reuse an application or DBA account.

## Deploy the Fabric capacity

```powershell
./deploy-customer.ps1 `
  -SubscriptionId       <subscription-id> `
  -ResourceGroupName    rg-call-analytics `
  -FabricCapacityName   fabriccallanalyticsf2 `
  -FabricCapacityAdmin  <admin-upn>
```

`F2` is the smallest paid SKU that supports Fabric Data Agents. Two operational
notes learned the hard way while building this:

- **F2 Spark capacity is very small.** Lakehouse writes go through Spark and can
  fail with `TooManyRequestsForCapacity` (HTTP 430) even for trivial inserts.
  Warehouse writes use T-SQL compute and are unaffected. Mirroring does not
  consume Spark capacity at all. For a demo, prefer Warehouse or mirrored tables
  over Spark-authored Lakehouse tables, or size above F2.
- **A paused capacity takes the Data Agent offline** with "Data agents are
  temporarily unavailable." Pausing between demos is the right way to control
  spend, but resume it before a customer session:

  ```powershell
  az resource invoke-action `
    --action resume `
    --ids /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Fabric/capacities/<name>
  ```

## Create the Oracle schema

Run `oracle-schema.sql` with SQLcl, SQL*Plus, or an approved migration tool, as
a schema owner rather than `SYS`. The script creates `call_analytics` and enables
table-level supplemental logging. Database-level prerequisites are listed as
comments rather than executed, because enabling archivelog restarts the
instance.

The schema deliberately uses `VARCHAR2(4000)` rather than `CLOB` for the
summary: Mirroring does not replicate LOB columns, so a `CLOB` summary would
silently fail to appear in OneLake.

## Configure Mirroring

Mirrored databases, connections, and gateways are Fabric tenant artifacts rather
than ARM resources, so they are created in the Fabric portal or through the
Fabric REST API, not through Bicep:

1. Install and register the on-premises data gateway on a host with network
   access to the Oracle listener.
2. In the Fabric workspace, create a **Mirrored Oracle database** item and point
   it at the gateway connection.
3. Select only the `call_analytics` table. Mirror the narrow analytics table,
   not the whole schema.
4. Wait for the initial snapshot, then confirm the replication status is running
   and the row count matches the source.

Store the Oracle replication credential in the Fabric connection or Key Vault.
Never commit it, and never pass it on a command line.

## Add the governed semantic model

The Data Agent can compute a rate by writing its own SQL, but nothing guarantees
it writes the same SQL twice, and nothing stops two teams defining "save rate"
differently. A semantic model fixes both: each KPI has one definition that every
consumer shares.

Create a Direct Lake semantic model over the Warehouse (**New semantic model**
in the Warehouse ribbon, select `call_analytics`), then apply the measures:

```bash
python apply_semantic_model_measures.py \
  --workspace-id <workspace-guid> \
  --semantic-model-id <semantic-model-guid>
```

`semantic-model-measures.tmdl` is the source of truth for the definitions and is
meant to be reviewed like code. The script is idempotent: re-running it replaces
the existing measures rather than duplicating them, so editing the TMDL and
re-applying is the normal workflow.

The measures cover four areas:

| Folder | Measures |
| --- | --- |
| Volume | Total Calls |
| Competitive | Competitor Mentions, Calls Mentioning Competitor, Competitive Pressure Rate |
| Churn | Cancellation Calls, Save Rate, At-Risk Calls, Monthly Revenue at Risk, Annual Revenue at Risk |
| Service Quality | Escalation Calls, Escalation Rate, Repeat Contact Calls, First Contact Resolution Rate |
| Cost | Avg Handle Time (sec), Total Handle Hours |

`Monthly Revenue at Risk` is the one worth demonstrating first. It is recurring
revenue on calls that both named a competitor and requested cancellation, which
requires transcript signals joined to billing data. A tool that only sees
transcripts cannot compute it at all.

Add the semantic model to the Data Agent as a second data source and route KPI
questions to it, leaving row-level and ad hoc questions on the Warehouse.

## Point the Data Agent at the mirrored data

1. Add the mirrored database (or a Warehouse view over it) as a Data Agent data
   source.
2. **Select the table explicitly.** Attaching a source is not the same as
   selecting its tables, and this applies to semantic models too. An
   attached-but-unselected table produces confident "no data found" answers,
   which is worse than an error because it looks like a real result. If the
   agent reports no data for a metric you can query directly, check the
   checkbox next to the table before changing anything else.
3. Add data-source instructions and example queries so routing is deterministic.
4. Publish. The published version is what other users and Copilot consume; draft
   changes do not take effect until you republish.

## Verify before demonstrating

```bash
python ../../data/eval_data_agent.py --source corpus
```

`data/eval_data_agent.py` computes a SQL baseline for a fixed question set and,
when pointed at a published agent, asks the same questions repeatedly and scores
answers for both correctness and run-to-run stability. This is the guard against
the failure that undermined the earlier executive demo, where the same question
returned a different competitor count on consecutive runs.

Run it after any change to the data, the schema selection, or the agent
instructions.
