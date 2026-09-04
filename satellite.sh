#!/usr/bin/env bash
# ===========================================================================
# satellite.sh — lanzador de la CLI unificada de Satellite (Python + C++).
#
# Uso (desde cualquier proyecto consumidor):
#     ./satellite.sh init
#     ./satellite.sh agents
#     ./satellite.sh run "objetivo"
#     ...
#
# Encapsula: el runtime Python (venv del framework), los binarios C++ de
# backend (satellite, satellite_agent_host, satellite_factory_cli) y el g++
# del toolchain si está disponible.
# ===========================================================================
set -euo pipefail

# Directorio raíz del framework (donde vive este script).
SAT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export SATELLITE_CPP_BIN="$SAT_ROOT/build/satellite"
export SATELLITE_AGENT_HOST="$SAT_ROOT/build/satellite_agent_host"
export SATELLITE_FACTORY_CLI="$SAT_ROOT/build/satellite_factory_cli"
export PYTHONPATH="$SAT_ROOT"

# g++ para compilar agentes generados, si el toolchain MSYS2/MinGW existe.
if [ -x "/mingw64/bin/g++.exe" ]; then
    export PATH="/mingw64/bin:$PATH"
fi

PYTHON_BIN="$SAT_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    # Windows/MSYS2: el venv usa Scripts/.
    PYTHON_BIN="$SAT_ROOT/.venv/Scripts/python.exe"
fi
if [ ! -x "$PYTHON_BIN" ]; then
    echo "Error: no se encontró el venv del framework en $SAT_ROOT/.venv" >&2
    exit 1
fi

exec "$PYTHON_BIN" -m satellite_py.cli "$@"
