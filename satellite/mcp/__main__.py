"""python -m satellite.mcp entrypoint (stdio)."""

from satellite.mcp.server import MCPServer


def main() -> None:
    """Arranca el servidor MCP por stdio (project root por env opcional)."""
    MCPServer().serve()


if __name__ == "__main__":
    main()
