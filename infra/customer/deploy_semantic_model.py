"""Deploy the call-analytics semantic model to a Fabric workspace from source.

Fabric semantic models are workspace items rather than ARM resources, so they
cannot be deployed with Bicep. This script closes that gap: the complete model
definition lives in ``semantic-model/`` as TMDL and is created or updated here
through the Fabric Items API. No portal steps are required, and the KPI
definitions are reviewed like any other code.

The TMDL layout matches what Fabric Git integration produces, so the same folder
can also be synced by connecting the workspace to a repository.

``definition/expressions.tmdl`` carries two placeholders that are substituted at
deploy time, because the Direct Lake source differs per environment:

    ${WAREHOUSE_SQL_ENDPOINT}   Warehouse SQL analytics endpoint host name
    ${WAREHOUSE_ID}             Warehouse item GUID

Both are discovered automatically from ``--warehouse-name`` when not supplied.

Authentication uses the Azure CLI login, so no secrets are handled here.

Examples:
    # create or update, resolving the Direct Lake source by name
    python deploy_semantic_model.py \
        --workspace-id <guid> --warehouse-name charter_call_warehouse

    # show what would change without writing
    python deploy_semantic_model.py \
        --workspace-id <guid> --warehouse-name charter_call_warehouse --what-if
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_RESOURCE = "https://api.fabric.microsoft.com"
DEFAULT_DEFINITION = Path(__file__).parent / "semantic-model"
DEFAULT_MODEL_NAME = "Charter Call Analytics Model"

# Files Fabric expects, relative to the definition root. `.platform` is written
# by the service on export but must not be uploaded on create, so it is excluded
# here and the display name is supplied through the item payload instead.
DEFINITION_FILES = (
    "definition.pbism",
    "definition/database.tmdl",
    "definition/model.tmdl",
    "definition/expressions.tmdl",
    "definition/tables/call_analytics.tmdl",
)


def get_token() -> str:
    # On Windows the CLI entry point is az.cmd, which CreateProcess will not
    # resolve from the bare name "az"; shutil.which finds the real file without
    # resorting to shell=True.
    from shutil import which

    executable = which("az")
    if executable is None:
        raise SystemExit("Azure CLI not found on PATH. Install it and run 'az login'.")
    result = subprocess.run(
        [executable, "account", "get-access-token", "--resource", FABRIC_RESOURCE,
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"Could not acquire a Fabric token. Run 'az login'.\n{result.stderr}")
    return result.stdout.strip()


def request(method: str, url: str, token: str, body: dict[str, Any] | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as response:
            payload = response.read().decode("utf-8")
            return response.status, dict(response.headers), (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} failed with HTTP {error.code}: {detail}") from error


def await_operation(status, headers, body, token, fetch_result: bool = True):
    """Resolve a Fabric long-running operation.

    Read operations expose a ``/result`` document; write operations do not and
    answer OperationHasNoResult, so the caller states which it expects.
    """
    if status not in (202,):
        return body
    location = headers.get("Location")
    if not location:
        raise SystemExit("Fabric returned 202 without a Location header.")
    for _ in range(80):
        time.sleep(3)
        _, _, state = request("GET", location, token)
        if state.get("status") == "Succeeded":
            if not fetch_result:
                return {}
            _, _, result = request("GET", f"{location}/result", token)
            return result
        if state.get("status") == "Failed":
            raise SystemExit(f"Fabric operation failed: {json.dumps(state)}")
    raise SystemExit("Timed out waiting for the Fabric operation to complete.")


def find_item(workspace_id: str, name: str, item_type: str, token: str) -> dict[str, Any] | None:
    _, _, body = request("GET", f"{FABRIC_API}/workspaces/{workspace_id}/items", token)
    for item in body.get("value", []):
        if item.get("displayName") == name and item.get("type") == item_type:
            return item
    return None


def warehouse_sql_endpoint(workspace_id: str, warehouse_id: str, token: str) -> str:
    _, _, body = request(
        "GET", f"{FABRIC_API}/workspaces/{workspace_id}/warehouses/{warehouse_id}", token
    )
    endpoint = (body.get("properties") or {}).get("connectionString")
    if not endpoint:
        raise SystemExit(
            "Could not read the Warehouse connection string. "
            f"Response was: {json.dumps(body)[:400]}"
        )
    return endpoint


def build_parts(definition_root: Path, substitutions: dict[str, str]) -> list[dict[str, str]]:
    parts: list[dict[str, str]] = []
    for relative in DEFINITION_FILES:
        source = definition_root / relative
        if not source.exists():
            raise SystemExit(f"Missing definition file: {source}")
        text = source.read_text(encoding="utf-8")
        for key, value in substitutions.items():
            text = text.replace(f"${{{key}}}", value)
        remaining = [k for k in substitutions if f"${{{k}}}" in text]
        if remaining:
            raise SystemExit(f"Unsubstituted placeholders in {relative}: {remaining}")
        parts.append(
            {
                "path": relative,
                "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                "payloadType": "InlineBase64",
            }
        )
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--warehouse-name", default="charter_call_warehouse")
    parser.add_argument("--warehouse-id", help="Overrides lookup by --warehouse-name.")
    parser.add_argument("--sql-endpoint", help="Overrides lookup of the Warehouse endpoint.")
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--what-if", action="store_true")
    args = parser.parse_args()

    token = get_token()

    warehouse_id = args.warehouse_id
    if not warehouse_id:
        warehouse = find_item(args.workspace_id, args.warehouse_name, "Warehouse", token)
        if warehouse is None:
            raise SystemExit(
                f"Warehouse '{args.warehouse_name}' not found in workspace {args.workspace_id}."
            )
        warehouse_id = warehouse["id"]

    endpoint = args.sql_endpoint or warehouse_sql_endpoint(args.workspace_id, warehouse_id, token)

    parts = build_parts(
        args.definition,
        {"WAREHOUSE_SQL_ENDPOINT": endpoint, "WAREHOUSE_ID": warehouse_id},
    )
    measure_count = sum(
        (args.definition / relative).read_text(encoding="utf-8").count("\n\tmeasure ")
        for relative in DEFINITION_FILES
        if relative.startswith("definition/tables/")
    )

    existing = find_item(args.workspace_id, args.model_name, "SemanticModel", token)
    action = "update" if existing else "create"
    print(f"Direct Lake source : {endpoint} / {warehouse_id}")
    print(f"Definition files   : {len(parts)}")
    print(f"Governed measures  : {measure_count}")
    print(f"Planned action     : {action} '{args.model_name}'")

    if args.what_if:
        print("what-if: no changes written.")
        return 0

    if existing:
        status, headers, body = request(
            "POST",
            f"{FABRIC_API}/workspaces/{args.workspace_id}/semanticModels/"
            f"{existing['id']}/updateDefinition?updateMetadata=True",
            token,
            {"definition": {"parts": parts}},
        )
        await_operation(status, headers, body, token, fetch_result=False)
        model_id = existing["id"]
    else:
        status, headers, body = request(
            "POST",
            f"{FABRIC_API}/workspaces/{args.workspace_id}/semanticModels",
            token,
            {"displayName": args.model_name, "definition": {"parts": parts}},
        )
        created = await_operation(status, headers, body, token, fetch_result=True) or body
        model_id = created.get("id", "unknown")

    print(f"Semantic model {action}d: {model_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
