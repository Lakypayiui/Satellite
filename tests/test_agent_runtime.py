"""Tests de LLMAgent con FakeLLM (determinista)."""

import asyncio
from pathlib import Path

from satellite.agents.base import AgentContext
from satellite.agents.runtime import LLMAgent
from satellite.core.result import AgentStatus
from satellite.core.spec import AgentSpec
from satellite.providers.fake import FakeLLM
from satellite.security.policy import DefaultSecurityPolicy
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import WriteFileTool
from satellite.tools.registry import DefaultToolRegistry
from satellite.core.tool_models import ToolResult


def run(coro):
    return asyncio.run(coro)


def make_spec(output_schema=None, max_turns=1) -> AgentSpec:
    return AgentSpec(
        id="html_writer",
        name="HTML Writer",
        capabilities=("write_html",),
        prompt_ref="html_writer",
        tools=("write_file",),
        output_schema=output_schema,
        max_turns=max_turns,
    )


def make_ctx(tmp_path: Path, feedback=()) -> AgentContext:
    return AgentContext(feedback=feedback)


def make_agent(spec, script, tmp_path: Path, tools=None) -> tuple[LLMAgent, FakeLLM]:
    provider = FakeLLM(script=script)
    registry = DefaultToolRegistry(policy=DefaultSecurityPolicy(allowed_tools={"write_file", "request_context"}))
    registry.register(WriteFileTool())
    agent = LLMAgent(
        spec=spec,
        system_prompt="Eres un escritor.\n{tools}\n{output_schema}",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        tools=registry.list_for(spec.tools),
        provider=provider,
        tool_registry=registry,
        policy=DefaultSecurityPolicy(allowed_tools={"write_file", "request_context"}),
        project_root=tmp_path,
    )
    return agent, provider


def test_write_file_and_synthesize(tmp_path: Path) -> None:
    spec = make_spec(output_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["path"],
    })
    script = [
        {"tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "out.html", "content": "<p>hola</p>"}}]},
        {"content": "{\"path\": \"out.html\", \"summary\": \"creado\"}"},
    ]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1", "goal": "crear html"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    assert (tmp_path / "out.html").exists()
    assert result.output == {"path": "out.html", "summary": "creado"}
    assert result.tool_calls[0].tool_name == "write_file"
    assert result.tool_calls[0].success is True


def test_output_schema_valid_json(tmp_path: Path) -> None:
    spec = make_spec(output_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["path", "summary"],
    })
    script = [{"content": "{\"path\": \"a.html\", \"summary\": \"ok\"}"}]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    assert result.output == {"path": "a.html", "summary": "ok"}


def test_output_schema_invalid_retries_then_fails(tmp_path: Path) -> None:
    spec = make_spec(output_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    })
    script = [
        {"content": "{\"sin_path\": 1}"},
        {"content": "{\"sin_path\": 2}"},  # reintento también malo → FAILED
    ]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.FAILED
    assert result.error is not None
    assert "schema" in result.error.lower() or "JSON" in result.error


def test_max_turns_respected(tmp_path: Path) -> None:
    spec = make_spec(max_turns=2)
    script = [
        {"tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}]},
        {"tool_calls": [{"id": "c2", "name": "write_file", "arguments": {"path": "b.txt", "content": "y"}}]},
        {"content": "{\"summary\": \"fin\"}"},
    ]
    agent, provider = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    # 2 rondas de tools + 1 síntesis = 3 llamadas
    assert len(provider.requests) == 3


def test_agent_with_tools_survives_metered_provider(tmp_path: Path) -> None:
    """Regresión: el bucle de tools no debe pisar el ``CallContext`` de la llamada.

    Con el proveedor envuelto en ``MeteredProvider`` (como en el runtime real), un
    ``ToolCall`` colado en ``request.call`` revienta con ``AttributeError`` en la
    segunda llamada y en la síntesis.
    """
    from satellite.observability.call_log import CallRecorder, default_log_dir
    from satellite.providers.metered import MeteredProvider

    spec = make_spec(max_turns=2)
    script = [
        {"tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}]},
        {"tool_calls": [{"id": "c2", "name": "write_file", "arguments": {"path": "b.txt", "content": "y"}}]},
        {"content": "{\"summary\": \"fin\"}"},
    ]
    agent, provider = make_agent(spec, script, tmp_path)
    recorder = CallRecorder(default_log_dir(tmp_path))
    agent.provider = MeteredProvider(provider, recorder, provider_name="fake", model="fake")

    result = run(agent.execute({"node_id": "n1", "goal": "escribir"}, make_ctx(tmp_path)))

    assert result.status is AgentStatus.OK
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").exists()
    # las 3 llamadas (2 rondas de tools + síntesis) conservan la identidad real
    records = recorder.load()
    assert len(records) == 3
    assert {record.role for record in records} == {spec.model_role}
    assert {record.agent_id for record in records} == {spec.id}


def test_simple_no_tools(tmp_path: Path) -> None:
    spec = make_spec()
    script = [{"content": "{\"summary\": \"sin tools\"}"}]
    agent, _ = make_agent(spec, script, tmp_path)
    result = run(agent.execute({"node_id": "n1"}, make_ctx(tmp_path)))
    assert result.status is AgentStatus.OK
    assert result.tool_calls == ()


def test_summary_is_the_llm_text_when_there_is_nothing_else() -> None:
    """Un nodo sin esquema resume con la respuesta del LLM, no con «Node completado»."""
    from satellite.agents.runtime import _summarize

    assert _summarize({}, {"content": "Esta carpeta contiene el framework."}, None) == (
        "Esta carpeta contiene el framework."
    )
    # sigue siendo una línea y con tope, para el log de eventos
    largo = _summarize({}, {"content": "x" * 500}, None)
    assert len(largo) == 200 and largo.endswith("…")
    assert "\n" not in _summarize({}, {"content": "uno\ndos"}, None)
    # si hay algo más específico, manda eso
    assert _summarize({}, {"path": "src/a.py", "content": "x"}, None) == "Trabajó en src/a.py"
    assert _summarize({}, {}, "boom") == "Node falló: boom"
    assert _summarize({}, {}, None) == "Node completado"


class _PuertoGrabador:
    """Puerto de aprobación de mentira: graba lo que le piden y decide."""

    def __init__(self, *, approve: bool = True, feedback: str = "") -> None:
        self.approve = approve
        self.feedback = feedback
        self.calls: list = []

    async def request(self, proposal, ctx):
        from satellite.core.proposal import ApprovalDecision, ApprovalStatus

        self.calls.append(ctx)
        return ApprovalDecision(
            status=ApprovalStatus.APPROVED if self.approve else ApprovalStatus.REJECTED,
            feedback=self.feedback,
        )


def _denegado_por_ruta(fuera: Path, proyecto: Path):
    from satellite.core.tool_models import ToolResult

    return ToolResult(
        call_id="c1",
        success=False,
        error=f"fuera del proyecto: {fuera}",
        error_code="path_outside_project",
        output={"path": str(fuera), "project_root": str(proyecto), "raw": str(fuera)},
    )


def _escribir_en(fuera: Path):
    from satellite.core.tool_models import ToolCall

    return ToolCall(id="c1", name="write_file", arguments={"path": str(fuera), "content": "hola"})


def test_a_path_outside_is_asked_and_retried_with_permission(tmp_path: Path) -> None:
    """La negativa deja de ser un callejón sin salida: se pregunta y, si sí, se hace."""
    from satellite.tools.base import ToolContext

    spec = make_spec()
    agent, _ = make_agent(spec, [], tmp_path)
    fuera = tmp_path.parent / "otra_zona" / "a.txt"
    (tmp_path.parent / "otra_zona").mkdir(exist_ok=True)
    port = _PuertoGrabador(approve=True, feedback="once")
    ctx = ToolContext(project_root=tmp_path, policy=agent.policy, approval=port)

    result = run(agent._ask_for_path(_escribir_en(fuera), ctx, _denegado_por_ruta(fuera, tmp_path)))

    assert len(port.calls) == 1
    assert port.calls[0].kind == "path"
    assert port.calls[0].details["path"] == str(fuera)
    assert result.success is True
    assert fuera.read_text(encoding="utf-8") == "hola"


def test_allowing_the_session_stops_asking(tmp_path: Path) -> None:
    from satellite.tools.base import ToolContext

    spec = make_spec()
    agent, _ = make_agent(spec, [], tmp_path)
    fuera = tmp_path.parent / "zona_sesion" / "b.txt"
    (tmp_path.parent / "zona_sesion").mkdir(exist_ok=True)
    port = _PuertoGrabador(approve=True, feedback="session")
    ctx = ToolContext(project_root=tmp_path, policy=agent.policy, approval=port)

    run(agent._ask_for_path(_escribir_en(fuera), ctx, _denegado_por_ruta(fuera, tmp_path)))

    assert fuera in tuple(agent.policy.allowed_paths)
    assert agent.policy.check_path(fuera, tmp_path) is True


def test_a_rejected_path_keeps_the_honest_error(tmp_path: Path) -> None:
    from satellite.tools.base import ToolContext

    spec = make_spec()
    agent, _ = make_agent(spec, [], tmp_path)
    fuera = tmp_path.parent / "rechazada" / "c.txt"
    port = _PuertoGrabador(approve=False)
    ctx = ToolContext(project_root=tmp_path, policy=agent.policy, approval=port)

    result = run(agent._ask_for_path(_escribir_en(fuera), ctx, _denegado_por_ruta(fuera, tmp_path)))

    assert len(port.calls) == 1
    assert result.success is False
    assert str(fuera) in (result.error or "")
    assert not fuera.exists()
