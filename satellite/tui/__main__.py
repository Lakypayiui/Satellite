"""python -m satellite.tui — lanza la interfaz interactiva."""

import os
from pathlib import Path

from satellite.tui.app import run_tui


def main() -> None:
    """Arranca la TUI (project root por argumento o env)."""
    import sys

    root = None
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    elif os.environ.get("SATELLITE_PROJECT_ROOT"):
        root = Path(os.environ["SATELLITE_PROJECT_ROOT"])
    run_tui(root)


if __name__ == "__main__":
    main()
