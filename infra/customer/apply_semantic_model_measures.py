"""Apply governed DAX measures to the Fabric semantic model.

Fabric semantic models are workspace items, not ARM resources, so they cannot be
deployed with Bicep. This script uses the Fabric Items API to read the model's
TMDL definition, append the measures defined in
``infra/customer/semantic-model-measures.tmdl`` to the fact table, and write the
definition back.

The operation is idempotent: measures already present in the model are replaced
rather than duplicated, so the script can be re-run after editing the TMDL
fragment.

Authentication uses the Azure CLI login (``az login``) via
``az account get-access-token``, so no secrets are handled here.

Usage:
    python infra/customer/apply_semantic_model_measures.py \
        --workspace-id <guid> --semantic-model-id <guid>
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_RESOURCE = "https://api.fabric.microsoft.com"
DEFAULT_MEASURES = Path(__file__).parent / "semantic-model-measures.tmdl"

# TMDL is indentation-sensitive. Table children sit at one tab; anything shallower
# would be parsed as a new top-level object.
TAB = "\t"


def get_token() -> str:
    # On Windows the Azure CLI entry point is az.cmd, which CreateProcess will
    # not resolve from the bare name "az". shutil.which finds the real file on
    # every platform and avoids shell=True.
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


def await_operation(
    status: int, headers: dict[str, str], body: Any, token: str, fetch_result: bool = True
) -> Any:
    """Resolve a Fabric long-running operation. Definition calls return 202.

    Read operations expose a ``/result`` document; write operations do not and
    answer OperationHasNoResult, so the caller says which it expects.
    """
    if status != 202:
        return body
    location = headers.get("Location")
    if not location:
        raise SystemExit("Fabric returned 202 without a Location header.")
    for _ in range(60):
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


def strip_existing(table_tmdl: str, measure_names: list[str]) -> str:
    """Remove previously applied measure blocks so re-runs do not duplicate them.

    A measure block starts at its leading ``///`` description lines (if any) and
    continues until the next line at the same indentation depth.
    """
    for name in measure_names:
        # Anchor on the '=' rather than a word boundary: measure names are
        # quoted, and \b never matches between a closing quote and a space, so
        # the previous block was silently never stripped and the model ended up
        # with two definitions of the same measure.
        pattern = re.compile(
            rf"^(?:{TAB}///.*\n)*{TAB}measure {re.escape(name)}\s*=.*?"
            rf"(?=^(?:{TAB}///|{TAB}(?:measure|column|partition|hierarchy|annotation)\b)|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        table_tmdl = pattern.sub("", table_tmdl)
    return table_tmdl


def measure_names(fragment: str) -> list[str]:
    return re.findall(r"^\s*measure\s+('[^']+'|\S+)\s*=", fragment, re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--semantic-model-id", required=True)
    parser.add_argument("--table", default="call_analytics")
    parser.add_argument("--measures", type=Path, default=DEFAULT_MEASURES)
    args = parser.parse_args()

    if not args.measures.exists():
        raise SystemExit(f"Measure file not found: {args.measures}")

    fragment = args.measures.read_text(encoding="utf-8")
    # Everything before the first measure is file-level commentary. Per-measure
    # `///` lines are TMDL descriptions and must be preserved.
    match = re.search(r"^\s*(?:///.*\n)*\s*measure\b", fragment, re.MULTILINE)
    if match is None:
        raise SystemExit("No measures found in the TMDL fragment.")
    measures_block = fragment[match.start():].strip("\n")
    names = measure_names(measures_block)
    if not names:
        raise SystemExit("No measures found in the TMDL fragment.")
    print(f"Applying {len(names)} measures: {', '.join(n.strip(chr(39)) for n in names)}")

    token = get_token()
    base = f"{FABRIC_API}/workspaces/{args.workspace_id}/semanticModels/{args.semantic_model_id}"

    status, headers, body = request("POST", f"{base}/getDefinition?format=TMDL", token)
    definition = await_operation(status, headers, body, token)["definition"]

    target = f"definition/tables/{args.table}.tmdl"
    parts = definition["parts"]
    part = next((p for p in parts if p["path"] == target), None)
    if part is None:
        raise SystemExit(
            f"Table part '{target}' not found. Available: {[p['path'] for p in parts]}"
        )

    table_tmdl = base64.b64decode(part["payload"]).decode("utf-8")
    table_tmdl = strip_existing(table_tmdl, names).rstrip("\n")
    updated = f"{table_tmdl}\n\n{measures_block}\n"
    part["payload"] = base64.b64encode(updated.encode("utf-8")).decode("ascii")
    part["payloadType"] = "InlineBase64"

    status, headers, body = request(
        "POST",
        f"{base}/updateDefinition?updateMetadata=True",
        token,
        {"definition": {"parts": parts}},
    )
    await_operation(status, headers, body, token, fetch_result=False)
    print(f"Semantic model updated with {len(names)} governed measures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
