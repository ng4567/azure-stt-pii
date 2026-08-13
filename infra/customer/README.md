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

What is reproducible from this folder, and what is not:

| Component | How it is deployed |
| --- | --- |
| Resource group and Fabric capacity | `main.bicep` via `deploy-customer.ps1` |
| Oracle source table and Mirroring prerequisites | `oracle-schema.sql` |
| Semantic model and all KPI measures | `semantic-model/` via `deploy_semantic_model.py` |
| Data Agent instructions | `data-agent-instructions.txt`, applied by `deploy_data_agent.py` |
| Data Agent item and source selection | `deploy_data_agent.py`, run in a Fabric notebook |
| Mirrored database and gateway connection | Portal; see [Configure Mirroring](#configure-mirroring) |

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

## Deploy the governed semantic model

The Data Agent can compute a rate by writing its own SQL, but nothing guarantees
it writes the same SQL twice, and nothing stops two teams defining "save rate"
differently. A semantic model fixes both: each KPI has one definition that every
consumer shares.

The entire model lives in source control under `semantic-model/` as TMDL, in the
same layout Fabric Git integration produces. Nothing here requires a portal
step:

```bash
python deploy_semantic_model.py \
  --workspace-id <workspace-guid> \
  --warehouse-name charter_call_warehouse

# preview without writing
python deploy_semantic_model.py --workspace-id <guid> --what-if
```

The script creates the model if it is absent and updates it in place if it
exists, so the same command serves first deployment and every later change. The
Direct Lake source is environment-specific, so `definition/expressions.tmdl`
carries `${WAREHOUSE_SQL_ENDPOINT}` and `${WAREHOUSE_ID}` placeholders that are
resolved from the workspace at deploy time; the script fails rather than
deploying if any placeholder is left unsubstituted.

Because the folder matches Fabric's Git format, a customer who prefers portal
workflows can instead connect the workspace to a repository and sync the same
files.

Measures are defined in `semantic-model/definition/tables/call_analytics.tmdl`
and are meant to be reviewed like code:

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

## Build the Data Agent

This is the step that turns the governed data into a question-and-answer
experience. Unlike the capacity and the semantic model, the Data Agent's
configuration is not committed to this repository, so the exact build is
recorded here. The reference deployment was built through the portal; the
code-first alternative is described at the end of this section.

### 1. Create the agent

In the workspace, choose **New item → Data agent** and name it, for example
`Charter Call Analytics Agent`. The item is created immediately and starts as an
unpublished draft.

If creation fails with *"An admin needs to change the SKU type for your
organization's Fabric capacity"*, the workspace is on a capacity that does not
support Data Agents. A Fabric trial capacity does not; F2 and above do. Assign
the workspace to the F2 capacity deployed earlier and retry.

### 2. Attach the data sources

Use **Add data → Data source** and attach both:

| Source | Purpose |
| --- | --- |
| `Charter Call Analytics Model` (semantic model) | Governed KPIs. NL2DAX resolves questions to the published measures. |
| `charter_call_warehouse` (Warehouse) | Row-level detail and ad hoc breakdowns that no measure covers. |

Attach the mirrored Oracle database instead of, or in addition to, the Warehouse
once Mirroring is configured; the agent treats a mirrored database as another
SQL source.

### 3. Select the tables — the step that is easy to miss

Expand each attached source and **tick the checkbox next to every table the
agent may query**. Attaching a source does not select its tables.

This is worth calling out because of how it fails. An attached-but-unselected
table does not raise an error. The agent answers:

> *"No data was found for competitor mentions, cancellations, or escalations
> across the available calls."*

That is a confident, well-formed, completely wrong answer, and in a live demo it
reads as "the data isn't there" rather than "the agent is misconfigured". It
happened twice while building this reference deployment: once on the Warehouse
and again on the semantic model, whose node is collapsed and unselected by
default after you attach it.

If the agent reports no data for something you can query directly in SQL or DAX,
check the table selection before changing anything else.

### 4. Write the agent instructions

Instructions are the routing layer. With two sources attached, the agent needs to
be told which one is authoritative for what. The exact text used by the reference
deployment is committed as
[`data-agent-instructions.txt`](data-agent-instructions.txt) — paste it into the
**Agent instructions** pane, or feed it to the SDK. Keeping it in the repository
means the routing rules are reviewable and diffable even though the agent item
itself is not.

It covers three things:

- **Routing.** The semantic model is authoritative for every business metric,
  rate, KPI, or ratio, and the measures are listed by name so the agent prefers
  them over recomputing arithmetic. The Warehouse handles row-level detail. The
  empty Lakehouse is explicitly excluded.
- **Definitions.** Terms whose meaning must not drift: `competitor` is a
  normalized name; `competitor_mentions` counts every mention, so it is always
  greater than or equal to the number of calls mentioning that competitor;
  save rate and revenue at risk are defined explicitly.
- **Rules.** Counts and rates come from aggregation or a governed measure and are
  never inferred by reading summary text. Applied filters and time periods are
  stated. Only PII-safe content is returned.

The listed measure names matter. Without them the agent tends to write its own
DAX or SQL for a metric that already has a governed definition, which is the
behaviour the semantic model exists to prevent.

The instructions pane renders markdown when it is not focused; click into the
text to edit it.

### 5. Publish

Publishing is what other users, Microsoft 365 Copilot, and the MCP endpoint
consume. Draft edits have no effect until you publish again, so republish after
every configuration change, including table selection.

### Code-first alternative

The [Fabric data agent Python SDK](https://learn.microsoft.com/fabric/data-science/fabric-data-agent-sdk)
covers the same management-plane operations, so the whole configuration above
can be a script instead of portal clicks.
[`deploy_data_agent.py`](deploy_data_agent.py) does exactly that: create or reuse
the agent, attach both sources, select their tables, apply
`data-agent-instructions.txt`, and publish.

**Run it in a Fabric notebook** in the target workspace:

```python
%pip install fabric-data-agent-sdk
# then run deploy_data_agent.py
```

What was and was not verified while writing this, so the constraints are not a
surprise:

| Step | Local (laptop) | Fabric notebook |
| --- | --- | --- |
| Install SDK | Works on Python 3.10–3.12 only | Works |
| Authenticate with `az login` | Works | Not needed |
| `create_data_agent` | **Verified working** | Works |
| Attach sources, select tables, publish | **Fails** | Works |

The local failure is not a bug in this script. Those calls resolve an internal
workload host through `synapse.ml.fabric.service_discovery`, a module that ships
only in the Fabric notebook runtime and is not published to PyPI, so they raise
`ModuleNotFoundError: No module named 'synapse'`. Supplying a substitute host
does not work either; the endpoint is internal.

Two environment issues are worth knowing before trying the local path:

- The SDK requires **Python >=3.10,<3.13**. On 3.13 or later `pip` refuses the
  install, and forcing it tries to build dependencies from source.
- The SDK pulls in `sempy`, which loads the .NET runtime. On **Windows on ARM**
  the interpreter is typically x64 while the installed .NET is `win-arm64`, and
  loading an ARM64 `hostfxr.dll` into an x64 process fails with `error 0xc1`.
  Install an x64 runtime and set `DOTNET_ROOT` to it; the script's docstring has
  the exact commands.

Fabric also supports [Git integration and deployment pipelines for Data
Agents](https://learn.microsoft.com/fabric/data-science/data-agent-source-control),
which is the supported way to version an agent's configuration and promote it
from development to production.

## Verify before demonstrating

```bash
python ../../data/eval_data_agent.py --source corpus
```

`data/eval_data_agent.py` computes a SQL baseline for a fixed question set and,
when pointed at a published agent, asks the same questions repeatedly and scores
answers for both correctness and run-to-run stability. This is the guard against
the failure that undermined the earlier executive demo, where the same question
returned a different competitor count on consecutive runs.

Run it after any change to the data, the table selection, or the agent
instructions.

For reference, the published agent answered these correctly against the
100-record corpus, with every figure matching the SQL and DAX baselines:

| Question | Answer |
| --- | --- |
| How many calls are in the table? | 3 calls (at the time; 100 after the full corpus load) |
| Competitor mentions by competitor, plus cancellation and escalation counts | Verizon 3 mentions / 1 escalation, AT&T 2 mentions / 1 cancellation |
| What is our save rate, and how much monthly revenue is at risk? | Save Rate 70.0%, Monthly Revenue at Risk $1,735 |
| Most-mentioned competitor, competitive pressure rate, annual revenue at risk | Four competitors tied at 33 mentions, 84.0%, $20,820 |

