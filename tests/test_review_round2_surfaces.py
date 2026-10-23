"""Segunda revisión: superficies y proveedores (docs/PLAN-correcciones.md).

Búsqueda en un worktree, cierre del servidor MCP al desconectarse el cliente, caída de
stream a ``complete`` sólo cuando el stream no está soportado, Ctrl+Q con vaciado de
resúmenes, el spec retirado fuera de los catálogos, ids de tarea repetidos, respuestas
tardías de elicitación, propuestas ya decididas y cuotas corruptas.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
from pathlib import Path

from satellite.tools.base import ToolContext
from satellite.tools.primitives import SearchFilesTool


def run(coro):
    return asyncio.run(coro)


# --- 1 · search_files dentro de un worktree -------------------------------------------


def test_search_files_lists_and_greps_relative_to_the_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / ".satellite" / "worktrees" / "feat"
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "a.py").write_text("x = 1\nneedle = 2\n", encoding="utf-8")
    ctx = ToolContext(project_root=tmp_path, workdir=worktree)

    listing = run(SearchFilesTool().execute({"pattern": "**/*.py"}, ctx))
    found = run(SearchFilesTool().execute({"pattern": "**/*.py", "content": "needle"}, ctx))

    assert [item["path"] for item in listing.output] == ["src/a.py"]
    assert found.output["files_scanned"] == 1
    assert [(m["path"], m["line"]) for m in found.output["matches"]] == [("src/a.py", 2)]


# --- 2 · EOF del cliente MCP cancela lo que corre -------------------------------------


def test_client_eof_cancels_the_runs_in_flight(tmp_path: Path) -> None:
    from satellite.mcp.server import MCPServer

    cancelled: list[tuple[str, str]] = []

    class _Orch:
        def cancel(self, task_id: str = "", reason: str = "") -> dict:
            cancelled.append((task_id, reason))
            return {"cancelled": True}

    class _Bundle:
        _active = {"t-largo": str(tmp_path)}

        def get(self, root):
            return {"orchestrator": _Orch()}

    server = MCPServer(bundle=_Bundle(), stdin=io.StringIO(""), stdout=io.StringIO())  # type: ignore[arg-type]

    server.serve()

    assert [task for task, _ in cancelled] == ["t-largo"]


# --- 9 · una respuesta tardía de elicitación no se contesta con un error ----------------


def test_a_late_elicitation_reply_is_dropped_silently() -> None:
    from satellite.mcp.server import MCPServer

    stdout = io.StringIO()
    late = json.dumps({"jsonrpc": "2.0", "id": "elicitation-7", "result": {"action": "accept"}})
    server = MCPServer(bundle=object(), stdin=io.StringIO(late + "\n"), stdout=stdout)  # type: ignore[arg-type]

    server.serve()

    assert stdout.getvalue() == ""


# --- 3 · el stream sólo cae a complete cuando el servidor no soporta streaming ------------


def test_only_a_stream_unsupported_error_triggers_the_fallback() -> None:
    from satellite.providers.openai_compat import _stream_unsupported

    class Err(Exception):
        def __init__(self, status: int, text: str) -> None:
            super().__init__(text)
            self.status_code = status

    assert _stream_unsupported(Err(400, "Unsupported parameter: stream_options")) is True
    assert _stream_unsupported(Err(400, "'stream' is not supported for this model")) is True
    assert _stream_unsupported(Err(429, "rate limit exceeded")) is False
    assert _stream_unsupported(Err(503, "stream service unavailable")) is False
    assert _stream_unsupported(Err(401, "invalid api key")) is False
    assert _stream_unsupported(ConnectionError("stream reset")) is False


# --- 4 · Ctrl+Q vacía los resúmenes como /quit -----------------------------------------


def test_ctrl_q_goes_through_the_flushing_quit() -> None:
    from textual.app import App

    from satellite.tui.app import SatelliteTUI

    assert SatelliteTUI.action_quit is not App.action_quit
    assert inspect.iscoroutinefunction(SatelliteTUI.action_quit)
    assert "_quit_after_flush" in inspect.getsource(SatelliteTUI.action_quit)


# --- 5 · el spec retirado no aparece en ningún catálogo ---------------------------------


def test_the_catalog_store_ignores_retired_specs(tmp_path: Path) -> None:
    from satellite.agents.store import AgentCatalogStore

    (tmp_path / "orchestrator.json").write_text(
        json.dumps({"id": "orchestrator", "name": "O", "capabilities": ["plan"], "prompt_ref": "orchestrator"}),
        encoding="utf-8",
    )
    (tmp_path / "orchestrator.system.txt").write_text("sys", encoding="utf-8")
    (tmp_path / "orchestrator.user.txt").write_text("{task}", encoding="utf-8")
    (tmp_path / "mine.json").write_text(
        json.dumps({"id": "mine", "name": "M", "capabilities": ["x"], "prompt_ref": "mine"}),
        encoding="utf-8",
    )
    store = AgentCatalogStore(tmp_path)

    assert [spec.id for spec in store.load_specs()] == ["mine"]
    assert "orchestrator" not in store.load_prompts()
    assert (tmp_path / "orchestrator.json").exists()  # no se borra nada del usuario


# --- 7 · un task_id elegido no puede pisar una tarea guardada ---------------------------


def test_a_client_task_id_cannot_reuse_a_persisted_task(tmp_path: Path) -> None:
    from satellite.mcp.handlers import get_handlers

    class _Store:
        def list_tasks(self):
            return ["t-hecha"]

    class _Bundle:
        _active: dict = {}

        def get(self, root):
            return {"orchestrator": None, "store": _Store()}

    out = run(
        get_handlers(_Bundle())["satellite_run"](  # type: ignore[arg-type]
            {"goal": "x", "project_root": str(tmp_path), "task_id": "t-hecha"}
        )
    )

    assert out["isError"] is True and "t-hecha" in out["text"]


# --- 10 · una propuesta decidida deja de estar «esperando» ------------------------------


def test_decided_proposals_are_no_longer_pending() -> None:
    from satellite.approval.base import ApprovalContext
    from satellite.approval.mcp import McpApproval

    def accept(_message, _schema, _timeout):
        return {"action": "accept", "content": {"approve": True}}

    port = McpApproval(elicitation=accept)
    ctx = ApprovalContext(kind="agent", task_id="t", summary="crear agente x")

    decision = run(port.request({"proposal_id": "prop1", "name": "x"}, ctx))

    assert decision.status.value == "approved"
    assert port.pending() == []  # aprobada y ejecutada: ya no espera a nadie


# --- 12 · una cuota corrupta no tumba la carga de la configuración -----------------------


def test_corrupt_quotas_fall_back_to_defaults(tmp_path: Path) -> None:
    from satellite.kernel.policy import Quotas, load_settings

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"quotas": {"max_agents": "many", "max_skills": 7}}), encoding="utf-8")

    settings = load_settings(cfg)

    assert settings.quotas.max_agents == Quotas().max_agents
    assert settings.quotas.max_skills == 7
