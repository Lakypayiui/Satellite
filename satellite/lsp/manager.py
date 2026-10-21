"""Per-project LSP manager: one client per server, shared across calls."""

from __future__ import annotations

import asyncio
import atexit
from pathlib import Path

from satellite.code.languages import normalize_language
from satellite.lsp.client import LspClient, LspError
from satellite.lsp.config import LspSettings, load_lsp_settings
from satellite.lsp.installer import (
    LspInstallError,
    install_server,
    resolve_command,
    status as installer_status,
)
from satellite.lsp.servers import (
    SERVERS,
    project_root_for,
    server_for_language,
)


class LspManager:
    def __init__(
        self, root: Path, settings: LspSettings | None = None
    ) -> None:
        self.root = Path(root).resolve()
        self.settings = settings or load_lsp_settings()
        self._clients: dict[tuple[str, Path], LspClient] = {}
        self._locks: dict[tuple[str, Path], asyncio.Lock] = {}
        self._errors: dict[str, str] = {}

    def _lock(self, server_id: str, server_root: Path) -> asyncio.Lock:
        key = (server_id, server_root)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def ensure(
        self,
        language: str,
        *,
        file_path: Path | None = None,
        force_install: bool = False,
    ) -> LspClient:
        language_id = normalize_language(language)
        if language_id is None:
            raise LspError(
                "lsp_unsupported_language",
                f"lenguaje no soportado: {language}",
            )
        server = server_for_language(language_id)
        if server is None:
            raise LspError(
                "lsp_unsupported_language",
                f"no hay servidor LSP para {language_id}",
            )
        server_root = (
            project_root_for(server, file_path, self.root)
            if file_path is not None
            else self.root
        )
        key = (server.id, server_root)
        existing = self._clients.get(key)
        if existing is not None and existing.alive:
            return existing
        async with self._lock(server.id, server_root):
            existing = self._clients.get(key)
            if existing is not None and existing.alive:
                return existing
            command = resolve_command(
                server.id,
                project_root=server_root,
                settings=self.settings,
            )
            if command is None:
                if not self.settings.auto_install and not force_install:
                    raise LspError(
                        "lsp_server_unavailable",
                        f"{server.name} no está instalado",
                        hint=server.install_hint,
                        server=server.id,
                    )
                try:
                    result = await install_server(
                        server.id,
                        settings=self.settings,
                        project_root=server_root,
                    )
                except LspInstallError as exc:
                    self._errors[server.id] = str(exc)
                    details = {
                        key: value
                        for key, value in exc.details.items()
                        if key not in ("code", "message")
                    }
                    raise LspError(exc.code, str(exc), **details) from exc
                command = result.command
                if command is None:
                    command = resolve_command(
                        server.id,
                        project_root=server_root,
                        settings=self.settings,
                    )
            if command is None:
                raise LspError(
                    "lsp_server_unavailable",
                    f"{server.name} no está disponible",
                    hint=server.install_hint,
                    server=server.id,
                )
            client = LspClient(
                command,
                server_root,
                language_id=server.languages[0],
                name=server.name,
            )
            try:
                await client.start()
            except LspError as exc:
                self._errors[server.id] = str(exc)
                details = {
                    key: value
                    for key, value in exc.details.items()
                    if key not in ("code", "message")
                }
                raise LspError(
                    exc.code,
                    str(exc),
                    stderr=client.stderr_tail,
                    **details,
                ) from exc
            self._clients[key] = client
            self._errors.pop(server.id, None)
            return client

    def client_for(self, language: str) -> LspClient | None:
        language_id = normalize_language(language)
        server = server_for_language(language_id or "")
        if server is None:
            return None
        for (server_id, _root), client in self._clients.items():
            if server_id == server.id and client.alive:
                return client
        return None

    def status(self) -> dict:
        servers = installer_status(
            project_root=self.root, settings=self.settings
        )
        for entry in servers:
            running = any(
                server_id == entry["id"] and client.alive
                for (server_id, _root), client in self._clients.items()
            )
            entry["running"] = running
            entry["last_error"] = self._errors.get(entry["id"], "")
        return {
            "root": str(self.root),
            "auto_install": self.settings.auto_install,
            "servers": servers,
        }

    async def install(self, language: str | None = None) -> list[dict]:
        if language is not None:
            language_id = normalize_language(language)
            server = server_for_language(language_id or "")
            if server is None:
                raise LspError(
                    "lsp_unsupported_language",
                    f"lenguaje no soportado: {language}",
                )
            targets = [server]
        else:
            targets = list(SERVERS.values())
        results: list[dict] = []
        for server in targets:
            try:
                result = await install_server(
                    server.id,
                    settings=self.settings,
                    project_root=self.root,
                )
                results.append(
                    {
                        "id": server.id,
                        "name": server.name,
                        "ok": True,
                        "command": list(result.command),
                        "stdout": result.stdout[-1000:],
                        "stderr": result.stderr[-1000:],
                    }
                )
                self._errors.pop(server.id, None)
            except LspInstallError as exc:
                self._errors[server.id] = str(exc)
                results.append(
                    {
                        "id": server.id,
                        "name": server.name,
                        "ok": False,
                        "error": str(exc),
                        "code": exc.code,
                        "hint": exc.details.get("hint", ""),
                    }
                )
        return results

    async def restart(self, language: str) -> bool:
        language_id = normalize_language(language)
        server = server_for_language(language_id or "")
        if server is None:
            return False
        stale = [
            key for key in self._clients if key[0] == server.id
        ]
        for key in stale:
            client = self._clients.pop(key, None)
            if client is not None:
                await client.shutdown()
        return True

    async def shutdown_all(self) -> None:
        for key, client in list(self._clients.items()):
            try:
                await client.shutdown()
            except Exception:  # noqa: BLE001 — shutdown must not raise
                client.terminate_sync()
            self._clients.pop(key, None)

    def shutdown_all_sync(self) -> None:
        for key, client in list(self._clients.items()):
            try:
                client.terminate_sync()
            except Exception:  # noqa: BLE001
                pass
            self._clients.pop(key, None)


_MANAGERS: dict[Path, LspManager] = {}


def manager_for(
    root: Path, settings: LspSettings | None = None
) -> LspManager:
    key = Path(root).resolve()
    manager = _MANAGERS.get(key)
    if manager is None:
        manager = LspManager(key, settings)
        _MANAGERS[key] = manager
    else:
        # Re-read the host config so toggling auto_install does not need a restart.
        manager.settings = settings or load_lsp_settings()
    return manager


async def shutdown_all() -> None:
    for manager in list(_MANAGERS.values()):
        await manager.shutdown_all()


def shutdown_all_sync() -> None:
    for manager in list(_MANAGERS.values()):
        manager.shutdown_all_sync()
    _MANAGERS.clear()


atexit.register(shutdown_all_sync)
