"""Bridge genérico al binario C++ ``satellite`` (unificación de capacidades).

La CLI Python es el ``satellite`` unificado; las capacidades que solo existen
en el C++ (init completo, agent create/test con compilación g++, context
inspect/get, ejecución de nativos 1-5 vía dispatch-step) se delegan a este
binario por subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def cpp_bin() -> str:
    """Ruta del binario C++ (env ``SATELLITE_CPP_BIN`` o ``./build/satellite``).

    En Windows se resuelve la extensión ``.exe`` si la ruta sin extensión no
    existe (``os.path.isfile`` no añade ``.exe`` automáticamente).
    """
    candidate = os.getenv("SATELLITE_CPP_BIN", "./build/satellite")
    if os.path.isfile(candidate):
        return candidate
    if os.name == "nt" and not candidate.lower().endswith(".exe"):
        with_exe = candidate + ".exe"
        if os.path.isfile(with_exe):
            return with_exe
    return candidate


def available() -> bool:
    """True cuando el binario C++ existe."""
    return os.path.isfile(cpp_bin())


def run_cpp(args: list[str], stdin_text: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run the C++ CLI with ``args`` (cwd = proyecto actual)."""
    return subprocess.run(
        [cpp_bin()] + args,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_cpp_json(args: list[str], request: dict[str, Any], timeout: int = 300) -> dict[str, Any]:
    """Run the C++ CLI sending a JSON request by stdin and parsing the JSON reply."""
    process = run_cpp(args, stdin_text=json.dumps(request) + "\n", timeout=timeout)
    output = process.stdout.strip()
    if not output:
        raise RuntimeError(f"satellite (C++) devolvió stdout vacío para {args}: {process.stderr.strip()}")
    result = json.loads(output)
    if not isinstance(result, dict):
        raise RuntimeError(f"satellite (C++) devolvió un JSON no-objeto para {args}")
    return result
