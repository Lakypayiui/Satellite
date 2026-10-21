"""Language Server Protocol client, server registry and auto-installer."""

from satellite.lsp.manager import manager_for, shutdown_all, shutdown_all_sync

__all__ = ["manager_for", "shutdown_all", "shutdown_all_sync"]
