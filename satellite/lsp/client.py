"""Minimal, dependency-free LSP client over stdio JSON-RPC."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from satellite.lsp.installer import _common_bins
from satellite.lsp.uris import path_to_uri, to_lsp_position
from satellite.sandbox.process import (
    IS_WINDOWS,
    group_kwargs,
    platform_argv,
    terminate_process_tree,
)


class LspError(Exception):
    def __init__(self, code: str, message: str = "", **details: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.details = details


class LspClient:
    """One language-server process for one project root."""

    def __init__(
        self,
        argv: tuple[str, ...],
        root: Path,
        *,
        language_id: str,
        name: str = "",
        request_timeout_s: float = 10.0,
    ) -> None:
        self.argv = tuple(argv)
        self.root = Path(root).resolve()
        self.language_id = language_id
        self.name = name or Path(argv[0]).name
        self.request_timeout_s = request_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, tuple[asyncio.Future, str]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._diagnostics: dict[str, list] = {}
        self._diagnostic_events: dict[str, asyncio.Event] = {}
        self._versions: dict[str, int] = {}
        self._opened: dict[str, str] = {}
        self._capabilities: dict = {}
        self._stderr_text = ""
        self._last_error = ""
        self._started = False
        self._closed = False

    def _spawn_argv(self) -> list[str]:
        return platform_argv(self.argv)

    # ---------------------------------------------------------------- lifecycle

    @property
    def alive(self) -> bool:
        return (
            self._proc is not None
            and self._proc.returncode is None
            and not self._closed
        )

    @property
    def stderr_tail(self) -> str:
        return self._stderr_text[-8000:]

    @property
    def last_error(self) -> str:
        return self._last_error

    async def start(self) -> None:
        if self.alive and self._started:
            return
        if self._proc is not None:
            await self._cleanup_process()
        env = os.environ.copy()
        path_parts = [str(part) for part in _common_bins()]
        path_parts.append(env.get("PATH", ""))
        env["PATH"] = os.pathsep.join(part for part in path_parts if part)
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._stderr_text = ""
        self._last_error = ""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._spawn_argv(),
                cwd=str(self.root),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **group_kwargs(),
            )
        except (OSError, ValueError) as exc:
            raise LspError(
                "lsp_server_unavailable",
                f"no se pudo iniciar {self.name}: {exc}",
                command=list(self.argv),
            ) from exc
        self._closed = False
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        try:
            result = await self.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "Satellite", "version": "0.2"},
                    "rootUri": path_to_uri(self.root),
                    "rootPath": str(self.root),
                    "workspaceFolders": [
                        {"uri": path_to_uri(self.root), "name": self.root.name}
                    ],
                    "capabilities": {
                        "workspace": {
                            "workspaceFolders": True,
                            "configuration": True,
                        },
                        "textDocument": {
                            "synchronization": {"didSave": True},
                            "hover": {"contentFormat": ["markdown", "plaintext"]},
                            "definition": {"linkSupport": True},
                            "references": {},
                            "documentSymbol": {
                                "hierarchicalDocumentSymbolSupport": True
                            },
                            "publishDiagnostics": {
                                "relatedInformation": True,
                                "versionSupport": True,
                            },
                        },
                        "window": {"workDoneProgress": True},
                    },
                },
                timeout=20.0,
            )
        except LspError:
            await self._cleanup_process()
            raise
        self._capabilities = (
            result.get("capabilities", {}) if isinstance(result, dict) else {}
        )
        await self.notify("initialized", {})
        self._started = True

    async def shutdown(self) -> None:
        if self._proc is None:
            return
        if not self.alive:
            await self._cleanup_process()
            return
        try:
            await self.request("shutdown", None, timeout=2.0)
        except (LspError, asyncio.TimeoutError):
            pass
        try:
            await self.notify("exit", None)
        except LspError:
            pass
        if self._proc.returncode is None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                await terminate_process_tree(self._proc)
        await self._cleanup_process()

    def terminate_sync(self) -> None:
        """Best-effort synchronous teardown for ``atexit``."""
        self._closed = True
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass

    async def _cleanup_process(self) -> None:
        self._closed = True
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        for future, _ in list(self._pending.values()):
            if not future.done():
                future.set_exception(
                    LspError("lsp_server_exited", "el servidor LSP se cerró")
                )
        self._pending.clear()
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
        self._reader_task = None
        self._stderr_task = None
        self._proc = None
        self._started = False

    # --------------------------------------------------------------- transport

    async def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise LspError("lsp_server_exited", "el servidor LSP no está vivo")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        message = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data
        async with self._write_lock:
            try:
                proc.stdin.write(message)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                raise LspError(
                    "lsp_server_exited", f"no se pudo escribir al servidor: {exc}"
                ) from exc

    async def _read_message(self) -> dict | None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        headers: dict[str, str] = {}
        while True:
            try:
                line = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                return None
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            try:
                key, _, value = line.decode("ascii", errors="replace").partition(":")
            except Exception:
                continue
            headers[key.strip().lower()] = value.strip()
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            return None
        if length <= 0:
            return None
        try:
            body = await proc.stdout.readexactly(length)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return None
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _reader_loop(self) -> None:
        while True:
            try:
                message = await self._read_message()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — never kill the loop silently
                self._last_error = str(exc)
                break
            if message is None:
                break
            if "id" in message and ("result" in message or "error" in message):
                pending = self._pending.pop(int(message["id"]), None)
                if pending is None:
                    continue
                future, _ = pending
                if future.done():
                    continue
                if "error" in message:
                    error = message.get("error") or {}
                    future.set_exception(
                        LspError(
                            "lsp_protocol_error",
                            str(error.get("message") or error),
                            data=error.get("data"),
                        )
                    )
                else:
                    future.set_result(message.get("result"))
                continue
            method = message.get("method")
            if not isinstance(method, str):
                continue
            if "id" in message:
                await self._answer_server_request(message)
            else:
                self._handle_notification(method, message.get("params"))
        reason = self.stderr_tail or "el servidor LSP terminó"
        self._last_error = reason
        for future, _ in list(self._pending.values()):
            if not future.done():
                future.set_exception(LspError("lsp_server_exited", reason))
        self._pending.clear()

    async def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            try:
                chunk = await proc.stderr.read(4096)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                return
            if not chunk:
                return
            self._stderr_text = (
                self._stderr_text + chunk.decode("utf-8", errors="replace")
            )[-8000:]

    def _handle_notification(self, method: str, params: Any) -> None:
        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            uri = str(params.get("uri") or "")
            diagnostics = params.get("diagnostics")
            if uri and isinstance(diagnostics, list):
                self._diagnostics[uri] = diagnostics
                event = self._diagnostic_events.get(uri)
                if event is not None:
                    event.set()
        elif method == "window/logMessage" and isinstance(params, dict):
            message = str(params.get("message") or "")
            if message:
                self._stderr_text = (self._stderr_text + message + "\n")[-8000:]

    async def _answer_server_request(self, message: dict) -> None:
        method = str(message.get("method") or "")
        params = message.get("params")
        if method == "workspace/configuration":
            items = params.get("items") if isinstance(params, dict) else []
            result = [{} for _ in (items or [])]
            # Pyright reads the interpreter from this section.
            if isinstance(items, list):
                for index, item in enumerate(items):
                    if isinstance(item, dict) and item.get("section") == "python":
                        result[index] = {"pythonPath": self._python_path()}
        elif method == "workspace/workspaceFolders":
            result = [
                {"uri": path_to_uri(self.root), "name": self.root.name}
            ]
        elif method == "workspace/applyEdit":
            result = {"applied": False}
        else:
            result = None
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
            )
        except LspError:
            pass

    def _python_path(self) -> str:
        for candidate in (
            self.root / ".venv" / "bin" / "python",
            self.root / "venv" / "bin" / "python",
            self.root / ".venv" / "Scripts" / "python.exe",
            self.root / "venv" / "Scripts" / "python.exe",
        ):
            if candidate.is_file():
                return str(candidate)
        return sys.executable

    # ----------------------------------------------------------------- requests

    async def request(
        self, method: str, params: Any = None, *, timeout: float | None = None
    ) -> Any:
        if not self.alive:
            await self.start()
        if self._proc is None:
            raise LspError("lsp_server_unavailable", f"{self.name} no está vivo")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, method)
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(
                future, timeout=timeout or self.request_timeout_s
            )
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            try:
                await self.notify("$/cancelRequest", {"id": request_id})
            except LspError:
                pass
            raise LspError(
                "lsp_timeout",
                f"{self.name} no respondió a {method} en {timeout or self.request_timeout_s}s",
            ) from exc

    async def notify(self, method: str, params: Any = None) -> None:
        await self._send(
            {"jsonrpc": "2.0", "method": method, "params": params}
        )

    # ----------------------------------------------------------------- documents

    async def open_document(
        self, path: Path, *, language_id: str | None = None
    ) -> str:
        target = Path(path)
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LspError(
                "lsp_file_not_found", f"no se pudo leer {target}: {exc}"
            ) from exc
        if len(text) > 2_000_000:
            raise LspError(
                "lsp_file_too_large", f"{target} supera 2 MB para LSP"
            )
        uri = path_to_uri(target)
        language = language_id or self.language_id
        version = self._versions.get(uri, 0)
        if uri not in self._opened:
            version += 1
            await self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language,
                        "version": version,
                        "text": text,
                    }
                },
            )
        elif self._opened.get(uri) != text:
            version += 1
            self._diagnostic_event(uri).clear()
            await self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        else:
            return uri
        self._opened[uri] = text
        self._versions[uri] = version
        return uri

    def _diagnostic_event(self, uri: str) -> asyncio.Event:
        event = self._diagnostic_events.get(uri)
        if event is None:
            event = asyncio.Event()
            self._diagnostic_events[uri] = event
        return event

    async def diagnostics(
        self,
        path: Path,
        *,
        language_id: str | None = None,
        wait_s: float = 3.0,
    ) -> tuple[list, bool]:
        uri = path_to_uri(path)
        event = self._diagnostic_event(uri)
        event.clear()
        uri = await self.open_document(path, language_id=language_id)
        if self._capabilities.get("diagnosticProvider"):
            try:
                result = await self.request(
                    "textDocument/diagnostic",
                    {"textDocument": {"uri": uri}},
                    timeout=max(wait_s, 3.0),
                )
                items, kind = _diagnostic_items(result)
                if items is not None:
                    if kind == "unchanged":
                        return self._diagnostics.get(uri, []), False
                    self._diagnostics[uri] = items
                    return items, False
            except LspError:
                pass
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.1, wait_s))
            return self._diagnostics.get(uri, []), False
        except asyncio.TimeoutError:
            return self._diagnostics.get(uri, []), True

    async def definition(
        self, path: Path, line: int, column: int, *, language_id: str | None = None
    ) -> Any:
        uri = await self.open_document(path, language_id=language_id)
        return await self.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": to_lsp_position(line, column),
            },
        )

    async def references(
        self,
        path: Path,
        line: int,
        column: int,
        *,
        include_declaration: bool = True,
        language_id: str | None = None,
    ) -> Any:
        uri = await self.open_document(path, language_id=language_id)
        return await self.request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": to_lsp_position(line, column),
                "context": {
                    "includeDeclaration": bool(include_declaration)
                },
            },
        )

    async def hover(
        self, path: Path, line: int, column: int, *, language_id: str | None = None
    ) -> Any:
        uri = await self.open_document(path, language_id=language_id)
        return await self.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": uri},
                "position": to_lsp_position(line, column),
            },
        )

    async def document_symbols(
        self, path: Path, *, language_id: str | None = None
    ) -> Any:
        uri = await self.open_document(path, language_id=language_id)
        return await self.request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )

    async def workspace_symbols(self, query: str) -> Any:
        return await self.request("workspace/symbol", {"query": query})


def _diagnostic_items(result: Any) -> tuple[list | None, str]:
    if result is None:
        return [], "full"
    if isinstance(result, list):
        return result, "full"
    if isinstance(result, dict):
        kind = str(result.get("kind") or "full")
        items = result.get("items")
        if isinstance(items, list):
            return items, kind
        if kind == "unchanged":
            return None, "unchanged"
    return None, "full"
