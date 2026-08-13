"""Build and publish the call-analytics Data Agent from source.

Fabric Data Agents are normally configured by clicking through the portal, which
leaves the most important part of the deployment undocumented: which sources are
attached, which tables are selected, and how questions are routed. This script
performs the whole configuration with the Fabric data agent Python SDK so it is
reviewable, diffable and repeatable.

It is idempotent. An agent of the same name is reconfigured in place rather than
duplicated.

Where to run this
-----------------
Run it in a **Fabric notebook** in the target workspace. That is the environment
the SDK is designed for.

Running it from a laptop is only partially supported. With ``az login`` plus the
credential shim below, ``create_data_agent`` succeeds, but attaching data sources
and publishing do not: those calls resolve an internal workload host through
``synapse.ml.fabric.service_discovery``, a module that ships only in the Fabric
notebook runtime and is not on PyPI. Locally those steps fail with
``ModuleNotFoundError: No module named 'synapse'``.

In a Fabric notebook, authentication is automatic and the whole script runs:

    %pip install fabric-data-agent-sdk

Local prerequisites, for the partial path
-----------------------------------------
The SDK supports Python >=3.10,<3.13:

    uv venv --python 3.12
    .venv/Scripts/python -m ensurepip --upgrade
    .venv/Scripts/python -m pip install fabric-data-agent-sdk azure-identity

The SDK pulls in ``sempy``, which loads the .NET runtime. On Windows on ARM the
interpreter is usually x64 while the installed .NET is win-arm64, and loading an
ARM64 ``hostfxr.dll`` into an x64 process fails with ``error 0xc1``. Install an
x64 runtime and point at it:

    dotnet-install.ps1 -Channel 8.0 -Runtime dotnet -Architecture x64 \
        -InstallDir $HOME/.dotnet-x64 -NoPath
    $env:DOTNET_ROOT = "$HOME/.dotnet-x64"

Example
-------
    python deploy_data_agent.py --workspace-id <guid>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_INSTRUCTIONS = Path(__file__).parent / "data-agent-instructions.txt"
DEFAULT_AGENT_NAME = "Charter Call Analytics Agent"
DEFAULT_PUBLISH_DESCRIPTION = (
    "Governed analytics over Charter call data. Business KPIs come from the "
    "Charter Call Analytics Model semantic model; row-level detail comes from "
    "the call analytics Warehouse."
)


def authenticate_outside_fabric() -> None:
    """Use the Azure CLI login. Not needed inside a Fabric notebook."""
    try:
        from azure.identity import AzureCliCredential
        from fabric.analytics.environment.credentials import (
            SetFabricAnalyticsDefaultTokenCredentialsGlobally,
        )
    except ImportError:
        return  # Running inside Fabric, where authentication is already handled.
    SetFabricAnalyticsDefaultTokenCredentialsGlobally(AzureCliCredential())


def get_or_create(agent_name: str, workspace_id: str | None):
    from fabric.dataagent.client import FabricDataAgentManagement, create_data_agent

    try:
        agent = FabricDataAgentManagement(agent_name, workspace_id)
        agent.get_configuration()  # fails here if the agent does not exist
        print(f"Found existing data agent '{agent_name}'.")
        return agent, False
    except Exception:
        print(f"Creating data agent '{agent_name}'...")
        return create_data_agent(agent_name, workspace_id), True


def attach(agent, artifact: str, workspace_id: str | None, table_path: list[str]) -> None:
    """Attach a source and select the table the agent is allowed to query.

    Selecting the table is not optional. A source that is attached but whose
    tables are unselected does not raise: the agent answers "no data found",
    which in a demo reads as missing data rather than misconfiguration.
    """
    print(f"  attaching '{artifact}'")
    try:
        datasource = agent.add_datasource(artifact, workspace_id)
    except Exception as error:
        print(f"    not newly added ({type(error).__name__}); reusing existing attachment")
        datasource = next((ds for ds in agent.get_datasources() if artifact in str(ds)), None)
        if datasource is None:
            raise
    print(f"    selecting {'/'.join(table_path)}")
    datasource.select(*table_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", help="Omit inside a Fabric notebook.")
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument("--semantic-model", default="Charter Call Analytics Model")
    parser.add_argument("--semantic-model-table", default="call_analytics")
    parser.add_argument("--warehouse", default="charter_call_warehouse")
    parser.add_argument("--warehouse-table", default="dbo.call_analytics")
    parser.add_argument("--instructions", type=Path, default=DEFAULT_INSTRUCTIONS)
    parser.add_argument("--publish-description", default=DEFAULT_PUBLISH_DESCRIPTION)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    if not args.instructions.exists():
        raise SystemExit(f"Instructions file not found: {args.instructions}")
    instructions = args.instructions.read_text(encoding="utf-8").strip()

    authenticate_outside_fabric()
    agent, created = get_or_create(args.agent_name, args.workspace_id)

    print("Configuring data sources...")
    # The semantic model carries the governed KPIs; the Warehouse carries
    # row-level detail. Semantic model tables are addressed by table name,
    # Warehouse tables by schema and table.
    attach(agent, args.semantic_model, args.workspace_id, [args.semantic_model_table])
    schema, _, table = args.warehouse_table.rpartition(".")
    attach(agent, args.warehouse, args.workspace_id, [schema or "dbo", table])

    print(f"Applying instructions ({len(instructions)} characters)...")
    agent.update_configuration(instructions=instructions)

    if args.no_publish:
        print("Draft configured. Skipping publish (--no-publish).")
        return 0

    print("Publishing...")
    agent.publish(description=args.publish_description)
    print(f"Data agent {'created' if created else 'updated'} and published: {args.agent_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
