"""``python -m satellite_py.web`` — serve the Satellite web console."""

from __future__ import annotations

import os

import uvicorn

PORT = int(os.getenv("SATELLITE_WEB_PORT", "7900"))


def main() -> None:
    uvicorn.run(
        "satellite_py.web.app:app",
        host=os.getenv("SATELLITE_WEB_HOST", "127.0.0.1"),
        port=PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
