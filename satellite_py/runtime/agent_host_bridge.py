"""Python bridge for executing an isolated Satellite agent host."""

import json
import subprocess
from typing import Any


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
    return result
