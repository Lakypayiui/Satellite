"""Python bridge for generating Satellite agents through the factory CLI."""

import json
import subprocess
from typing import Any


def create_agent(
    goal: str,
    capability: str,
    factory_bin: str = "./build/satellite_factory_cli",
) -> dict[str, Any]:
    """Generate an agent and return the factory CLI JSON response."""
    request = {"goal": goal, "capability": capability}
    process = subprocess.run(
        [factory_bin],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"satellite_factory_cli failed ({process.returncode}): "
            f"{process.stderr.strip() or process.stdout.strip()}"
        )

    output = process.stdout.strip()
    if not output:
        raise RuntimeError("satellite_factory_cli returned empty stdout")

    result = json.loads(output)
    if not isinstance(result, dict):
        raise RuntimeError("satellite_factory_cli returned a non-object JSON value")
    return result
