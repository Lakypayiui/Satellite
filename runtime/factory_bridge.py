"""Python bridge for generating Satellite agents through the factory CLI."""

import json
import os
import shutil
import subprocess
from typing import Any


def _compiler_env() -> dict[str, str]:
    """Asegura que g++ (MSYS2) esté en el PATH del subprocess de la factory."""
    env = dict(os.environ)
    if shutil.which("g++"):
        return env
    candidates = [
        r"C:\msys64\mingw64\bin",
        r"C:\msys2\mingw64\bin",
        "/usr/bin",
    ]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "g++.exe")) or os.path.isfile(os.path.join(candidate, "g++")):
            env["PATH"] = candidate + os.pathsep + env.get("PATH", "")
            break
    return env


def _factory_bin(factory_bin: str | None) -> str:
    if factory_bin is None:
        candidate = os.getenv("SATELLITE_FACTORY_CLI", "./build/satellite_factory_cli")
        if os.name == "nt" and not candidate.lower().endswith(".exe") and not os.path.isfile(candidate):
            with_exe = candidate + ".exe"
            if os.path.isfile(with_exe):
                candidate = with_exe
        factory_bin = candidate
    return factory_bin


def _run_factory(request: dict[str, Any], factory_bin: str | None, cwd: str | os.PathLike | None) -> dict[str, Any]:
    binary = _factory_bin(factory_bin)
    process = subprocess.run(
        [binary],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        cwd=cwd,
        env=_compiler_env(),
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


def create_agent(
    goal: str,
    capability: str,
    factory_bin: str | None = None,
    cwd: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Generate an agent (LLM inside the factory) and return the JSON response.

    Legacy mode: the factory C++ generates the spec with its internal LLM
    (DeepSeek). Prefer :func:`create_agent_from_spec` (spec generated in
    Python with the configured provider).
    """
    return _run_factory({"goal": goal, "capability": capability}, factory_bin, cwd)


def create_agent_from_spec(
    spec: dict[str, Any],
    factory_bin: str | None = None,
    cwd: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Compile/test/register a complete ``AgentSpec`` through the factory.

    ``spec`` must include name, description, version, input_schema,
    output_schema, context_requirements, capabilities, implementation_code
    and test_cases. The factory only compiles (no LLM involved).
    """
    return _run_factory({"spec": spec}, factory_bin, cwd)
