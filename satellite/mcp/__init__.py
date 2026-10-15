"""MCP over stdio: thin JSON-RPC 2.0 facade over the runtime (zero deps)."""

from satellite.mcp.server import MCPServer
from satellite.mcp.handlers import get_handlers

__all__ = ["MCPServer", "get_handlers"]
