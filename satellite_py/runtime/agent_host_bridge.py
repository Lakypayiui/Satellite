"""Python bridge for executing an isolated Satellite agent host."""

import json
import subprocess
from typing import Any

# Mapeo del enum AgentStatus C++ a strings (el host devuelve el número).
_CPP_STATUS_NAMES = {
    0: "IDLE",
    1: "RUNNING",
    2: "SUCCESS",
    3: "FAILED",
    4: "UNKNOWN_AGENT",
    5: "VALIDATION_ERROR",
    6: "DISABLED",
    7: "TIMEOUT",
}


def _normalize_status(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("status"), int):
        result["status"] = _CPP_STATUS_NAMES.get(result["status"], "UNKNOWN")
    return result


def run_agent(
    library_path: str,
    request: dict[str, Any],
    agent_host_bin: str = "./build/satellite_agent_host",
) -> dict[str, Any]:
    """Run one agent request through the isolated C++ host process."""
    process = subprocess.run(
        [agent_host_bin, library_path],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"agent_host failed ({process.returncode}): {process.stderr.strip()}"
        )

    output = process.stdout.strip()
    if not output:
        raise RuntimeError("agent_host returned empty stdout")

    result = json.loads(output)
    if not isinstance(result, dict):
        raise RuntimeError("agent_host returned a non-object JSON value")
    return _normalize_status(result)
