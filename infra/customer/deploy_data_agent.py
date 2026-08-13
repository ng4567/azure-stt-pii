"""Build and publish the call-analytics Data Agent from source.

Fabric Data Agents are normally configured by clicking through the portal, which
leaves the most important part of the deployment undocumented: which sources are
attached, which tables are selected, and how questions are routed. This script
performs the whole configuration with the Fabric data agent Python SDK so it is
reviewable, diffable and repeatable.

It is idempotent. An agent of the same name is reconfigured in place rather than
duplicated, and selecting an already-selected table is a no-op.

Public API only
---------------
The SDK exposes two generations of methods. The older ones (``get_datasources``,
``get_configuration``, ``update_configuration``, ``publish``) are marked
deprecated and route through an internal workload host resolved by
``synapse.ml.fabric.service_discovery`` - a module that ships only in the Fabric
notebook runtime and is not on PyPI. Calling them outside Fabric fails with
``ModuleNotFoundError: No module named 'synapse'``.

This script deliberately uses only the newer staging API, which runs against the
public Fabric endpoint:

    add_staging_datasource   attach a Lakehouse, Warehouse or semantic model
    list_datasources         enumerate attached sources
    get_elements             walk the schema tree of a source
    update_element           select a table
    patch_staging_settings   set the agent instructions
    publish_staging          publish the staged configuration

As a result the script runs from a laptop as well as from a Fabric notebook.

Prerequisites
-------------
The SDK supports Python >=3.10,<3.13:

    uv venv --python 3.12
    .venv/Scripts/python -m ensurepip --upgrade
    .venv/Scripts/python -m pip install fabric-data-agent-sdk azure-identity

Sign in with ``az login`` first. Inside a Fabric notebook, install with
``%pip install fabric-data-agent-sdk``; authentication is automatic and
``--workspace-id`` can be omitted.

The SDK pulls in ``sempy``, which loads the .NET runtime. On Windows on ARM the
Python interpreter is usually x64 while the installed .NET is win-arm64, and
loading an ARM64 ``hostfxr.dll`` into an x64 process fails with ``error 0xc1``.
Install an x64 runtime and point at it:

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
from typing import Any

DEFAULT_INSTRUCTIONS = Path(__file__).parent / "data-agent-instructions.txt"
DEFAULT_AGENT_NAME = "Charter Call Analytics Agent"
DEFAULT_PUBLISH_DESCRIPTION = (
    "Governed analytics over Charter call data. Business KPIs come from the "
    "Charter Call Analytics Model semantic model; row-level detail comes from "
    "the call analytics Warehouse."
)

# Element types that are containers to recurse into rather than selectable leaves.
CONTAINER_TYPES = {"Schemas", "Schema", "Tables", "Folder"}
# Guard against a malformed tree sending the walk unbounded.
MAX_DEPTH = 6


def authenticate_outside_fabric() -> None:
    """Use the Azure CLI login. A no-op inside a Fabric notebook."""
    try:
        from azure.identity import AzureCliCredential
        from fabric.analytics.environment.credentials import (
            SetFabricAnalyticsDefaultTokenCredentialsGlobally,
        )
    except ImportError:
        return  # Inside Fabric, where authentication is already handled.
    SetFabricAnalyticsDefaultTokenCredentialsGlobally(AzureCliCredential())


def get_or_create(agent_name: str, workspace_id: str | None):
    from fabric.dataagent.client import FabricDataAgentManagement, create_data_agent

    try:
        agent = FabricDataAgentManagement(agent_name, workspace_id)
        agent.get_settings()  # public-path probe; fails if the agent is absent
        print(f"Found existing data agent '{agent_name}'.")
        return agent, False
    except Exception:
        print(f"Creating data agent '{agent_name}'...")
        return create_data_agent(agent_name, workspace_id), True


def select_tables(datasource: Any, table_names: set[str] | None, root: str | None = None,
                  depth: int = 0) -> list[str]:
    """Walk a source's schema tree and select its tables.

    Selecting tables is not optional and is the single easiest step to miss. A
    source that is attached but whose tables are unselected does not raise: the
    agent answers "no data found", which in a demo reads as missing data rather
    than as misconfiguration.

    The tree is expanded lazily. A Warehouse initially reports only a `Schemas`
    node, and its schemas and tables appear only once that node has been
    fetched, so a freshly attached source can return an empty level on the first
    pass. `select_all` retries for that reason; do not collapse it into a single
    walk.

    Pass ``table_names`` to restrict selection, or None to select every table.
    """
    if depth > MAX_DEPTH:
        return []
    selected: list[str] = []
    for element in datasource.get_elements(root_id=root).get("value", []):
        name = element.get("displayName")
        kind = element.get("type")
        if kind == "Table":
            if table_names is None or name in table_names:
                result = datasource.update_element(element["id"], is_selected=True)
                if result.get("isSelected"):
                    selected.append(name)
        elif kind in CONTAINER_TYPES:
            selected += select_tables(datasource, table_names, element["id"], depth + 1)
    return selected


def select_all(agent: Any, table_names: set[str], expected_sources: int,
               attempts: int = 6, delay_seconds: int = 5) -> int:
    """Select the requested tables across every attached source.

    Two forms of eventual consistency make a single pass unreliable, and both
    were observed against a live workspace:

    * Immediately after ``add_staging_datasource``, ``list_datasources`` can
      return fewer sources than were attached - sometimes none at all.
    * A source that is listed can still report an unexpanded schema tree, so a
      Warehouse briefly shows only its ``Schemas`` node with nothing beneath it.

    The loop therefore retries until every expected source is both listed and
    has contributed a selection. Stopping earlier silently leaves a source with
    no selected tables, which produces the "no data found" answer this whole
    function exists to prevent.
    """
    import time

    per_source: dict[str, list[str]] = {}
    for attempt in range(1, attempts + 1):
        per_source = {}
        for datasource in agent.list_datasources():
            per_source[str(datasource)] = select_tables(datasource, table_names)

        resolved = sum(1 for names in per_source.values() if names)
        if resolved >= expected_sources:
            break
        if attempt < attempts:
            print(
                f"  {resolved}/{expected_sources} source(s) resolved; "
                f"retrying ({attempt}/{attempts - 1})"
            )
            time.sleep(delay_seconds)

    for names in per_source.values():
        for name in names:
            print(f"  selected '{name}'")
    return sum(len(names) for names in per_source.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", help="Omit inside a Fabric notebook.")
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Artifact to attach; repeatable. Defaults to the semantic model and the Warehouse.",
    )
    parser.add_argument(
        "--table",
        action="append",
        default=None,
        help="Table name to select; repeatable. Defaults to call_analytics.",
    )
    parser.add_argument("--instructions", type=Path, default=DEFAULT_INSTRUCTIONS)
    parser.add_argument("--publish-description", default=DEFAULT_PUBLISH_DESCRIPTION)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    sources = args.source or ["Charter Call Analytics Model", "charter_call_warehouse"]
    tables = set(args.table or ["call_analytics"])

    if not args.instructions.exists():
        raise SystemExit(f"Instructions file not found: {args.instructions}")
    instructions = args.instructions.read_text(encoding="utf-8").strip()

    authenticate_outside_fabric()
    agent, created = get_or_create(args.agent_name, args.workspace_id)

    print("Attaching data sources...")
    for source in sources:
        try:
            agent.add_staging_datasource(source, args.workspace_id)
            print(f"  attached '{source}'")
        except Exception as error:
            # Re-attaching an existing source is not fatal; selection still runs.
            print(f"  '{source}' not newly attached ({type(error).__name__}); continuing")

    print("Selecting tables...")
    total = select_all(agent, tables, expected_sources=len(sources))
    if total == 0:
        raise SystemExit(
            "No tables were selected. The agent would answer 'no data found' for "
            f"every question. Check that {sorted(tables)} exist in the attached sources."
        )
    print(f"  {total} table selection(s) applied across {len(sources)} source(s)")

    print(f"Applying instructions ({len(instructions)} characters)...")
    agent._client.patch_staging_settings({"aiInstructions": instructions})

    if args.no_publish:
        print("Draft configured. Skipping publish (--no-publish).")
        return 0

    print("Publishing...")
    agent.publish_staging(description=args.publish_description)
    print(f"Data agent {'created' if created else 'updated'} and published: {args.agent_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
