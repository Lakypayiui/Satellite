"""Fase C del plan de correcciones (docs/PLAN-correcciones.md), punto 3.

Crear un agente desde el chat pasa por el expander (proponer → validar → aprobar →
registrar). Antes el piloto delegaba en ``agent_creator``, que sólo devolvía la
propuesta: el agente no llegaba a existir aunque la delegación saliera bien.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.agents.expander import ExpansionOutcome
from satellite.cli import runtime_builder
from satellite.core.spec import AgentSpec
from satellite.orchestration.chat import ChatSession
from satellite.providers.fake import FakeLLM
from satellite.tools.base import ToolContext
from satellite.tools.create_agent import CreateAgentTool


def run(coro):
    return asyncio.run(coro)


class _Expander:
    def __init__(self, outcome: ExpansionOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    async def expand(self, need, **kwargs):
        self.calls.append({"need": need, **kwargs})
        return self.outcome


def _spec(agent_id: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id, name="Resumidor", description="Resume PDFs", capabilities=("summarize",),
        prompt_ref=agent_id,
    )


def test_the_tool_creates_through_the_expander_with_the_turn_port(tmp_path: Path) -> None:
    expander = _Expander(ExpansionOutcome(kind="created", spec=_spec("pdf_summarizer")))
    tool = CreateAgentTool(lambda: expander)
    port = object()

    result = run(
        tool.execute(
            {"need": "resumir PDFs largos"},
            ToolContext(project_root=tmp_path, task_id="t1", approval=port),
        )
    )

    assert result.success is True
    assert result.output["kind"] == "created"
    assert result.output["agent_id"] == "pdf_summarizer"
    [call] = expander.calls
    assert call["need"] == "resumir PDFs largos"
    assert call["approval_port"] is port
    assert call["task_id"] == "t1"


def test_a_rejected_or_unclear_creation_is_not_a_success(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    rejected = run(
        CreateAgentTool(lambda: _Expander(ExpansionOutcome(kind="rejected", reason="no"))).execute(
            {"need": "x"}, ctx
        )
    )
    unclear = run(
        CreateAgentTool(
            lambda: _Expander(ExpansionOutcome(kind="needs_clarification", question="¿qué PDFs?"))
        ).execute({"need": "x"}, ctx)
    )
    reuse = run(
        CreateAgentTool(
            lambda: _Expander(ExpansionOutcome(kind="reuse", reuse_candidate="python_coder"))
        ).execute({"need": "x"}, ctx)
    )

    assert rejected.success is False and rejected.output["kind"] == "rejected"
    assert unclear.success is False and unclear.output["question"] == "¿qué PDFs?"
    assert reuse.success is True and reuse.output["agent_id"] == "python_coder"


def test_without_an_expander_the_tool_says_so(tmp_path: Path) -> None:
    result = run(CreateAgentTool().execute({"need": "x"}, ToolContext(project_root=tmp_path)))
    assert result.success is False


def _runtime(tmp_path: Path, monkeypatch, script: list[dict]):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"name": "fake", "model": "m"}), encoding="utf-8")
    provider = FakeLLM(script=script, default_content="")
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: FakeLLM(default_content="r") if role == "context" else default,
    )
    orchestrator, _bus, _store, _registry = runtime_builder.build_runtime(project)
    return project, orchestrator, provider


def test_the_pilot_creates_agents_with_the_tool_not_by_delegating(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            {"tool_calls": [{"id": "c1", "name": "create_agent", "arguments": {"need": "resumir PDFs"}}]},
            {"content": "Creé el agente pdf_summarizer."},
        ],
    )
    expander = _Expander(ExpansionOutcome(kind="created", spec=_spec("pdf_summarizer")))
    orchestrator.expander = expander

    reply = run(ChatSession(project, orchestrator).send("crea un agente que resuma PDFs"))

    assert reply.status == "ok"
    assert len(expander.calls) == 1
    system = provider.requests[0].messages[0].content
    assert "- agent_creator:" not in system  # ya no es destino de delegación
    assert "create_agent" in system
