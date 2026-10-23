"""El agente piloto conduce el turno (docs/PLAN-flujo-piloto.md, fase 2)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.cli import runtime_builder
from satellite.core.events import EventType
from satellite.core.spec import AgentSpec
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.delegation import DelegationManager
from satellite.orchestration.pilot import PilotOrchestrator
from satellite.providers.fake import FakeLLM


def run(coro):
    return asyncio.run(coro)


def _runtime(tmp_path: Path, monkeypatch, script: list[dict]):
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "auth.py").write_text(
        "def login(user, password):\n    return check(user, password)\n", encoding="utf-8"
    )
    cfg = project / ".satellite" / "config"
    cfg.mkdir(parents=True)
    (cfg / "config.json").write_text(
        json.dumps(
            {"name": "fake", "model": "fake-model"}
        ),
        encoding="utf-8",
    )
    provider = FakeLLM(script=script, default_content="")
    resumidor = FakeLLM(default_content="resumen")
    monkeypatch.setattr(runtime_builder, "_load_provider", lambda _root: provider)
    monkeypatch.setattr(
        runtime_builder,
        "_role_provider",
        lambda _root, role, default: resumidor if role == "context" else default,
    )
    orchestrator, bus, _store, _registry = runtime_builder.build_runtime(project)
    events: list = []
    bus.subscribe(events.append)
    return project, orchestrator, events, provider


def _call(call_id: str, name: str, **arguments) -> dict:
    return {"tool_calls": [{"id": call_id, "name": name, "arguments": arguments}]}


def test_pilot_reads_the_code_before_answering(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call("c1", "read_file", path="auth.py"),
            {"content": "El login está en auth.py: delega en check(user, password)."},
        ],
    )
    assert isinstance(orchestrator, PilotOrchestrator)
    chat = ChatSession(project, orchestrator)

    reply = run(chat.send("¿cómo funciona el login?"))

    assert reply.status == "ok"
    assert reply.text == "El login está en auth.py: delega en check(user, password)."
    # El modelo recibió el contenido real del archivo como resultado de la tool.
    tool_message = provider.requests[1].messages[-1]
    assert "def login" in tool_message.content
    kinds = [event.event_type for event in events]
    assert EventType.TOOL_CALLED in kinds
    assert EventType.GRAPH_BUILT not in kinds  # sin planner ni grafo
    # Dos segmentos de texto: la narración previa a la tool (vacía) y la respuesta.
    deltas = [e for e in events if e.event_type is EventType.TEXT_DELTA]
    assert "".join(e.payload["text"] for e in deltas) == reply.text
    assert {e.payload.get("segment") for e in deltas} == {2}


def test_pilot_sees_the_catalog_but_not_itself(tmp_path: Path, monkeypatch) -> None:
    project, orchestrator, _events, provider = _runtime(
        tmp_path, monkeypatch, [{"content": "¡Hola!"}]
    )
    run(ChatSession(project, orchestrator).send("hola"))
    system = provider.requests[0].messages[0].content
    assert "# DELEGATION" in system
    assert "- python_coder:" in system
    assert "- web_researcher:" in system
    assert "- pilot:" not in system
    assert "- orchestrator:" not in system
    tools = {tool.name for tool in provider.requests[0].tools}
    assert {"read_file", "search_files", "lsp", "delegate"} <= tools
    assert not tools & {"write_file", "edit_file", "run_command", "spawn_agent"}


def test_pilot_delegates_a_change_without_an_escalation_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    coder_output = {
        "summary": "Añadí validación de contraseña vacía.",
        "files_changed": ["auth.py"],
        "verification": {"command": "pytest", "passed": True, "details": "3 passed"},
    }
    project, orchestrator, events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call(
                "c1",
                "delegate",
                agent_id="python_coder",
                task={"goal": "Reject empty passwords in auth.login"},
            ),
            {"content": json.dumps(coder_output)},
            {"content": "Listo: auth.py ahora rechaza contraseñas vacías. Tests: 3 passed."},
        ],
    )
    chat = ChatSession(project, orchestrator)

    reply = run(chat.send("haz que login rechace contraseñas vacías"))

    assert reply.status == "ok"
    assert reply.text.startswith("Listo")
    kinds = [event.event_type for event in events]
    assert EventType.DELEGATION_STARTED in kinds
    assert EventType.POLICY_VIOLATION not in kinds  # no se pidió ni negó escalado
    # El piloto recibió el resultado estructurado del coder, no sólo un resumen.
    delegated = json.loads(provider.requests[2].messages[-1].content)
    assert delegated["success"] is True
    assert delegated["output"]["details"]["files_changed"] == ["auth.py"]
    assert reply.output["delegations"] == [
        {"agent_id": "python_coder", "ok": True, "status": "ok", "error": None}
    ]
    # El coder trabaja sin narrar al chat: todos los deltas son del piloto.
    agents = {
        e.payload.get("agent_id") for e in events if e.event_type is EventType.TEXT_DELTA
    }
    assert agents == {"pilot"}


def test_a_delegated_diagram_reaches_the_reply(tmp_path: Path, monkeypatch) -> None:
    svg = str(tmp_path / "project" / "flow.svg")
    project, orchestrator, _events, _provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call(
                "c1",
                "delegate",
                agent_id="pele_diagram_agent",
                task={"goal": "Flowchart of the login flow"},
            ),
            {
                "content": json.dumps(
                    {
                        "mermaid": "flowchart LR\n  A --> B",
                        "rendered": True,
                        "svg_path": svg,
                        "diagram_type": "flowchart",
                    }
                )
            },
            {"content": "Aquí tienes el diagrama del login."},
        ],
    )
    chat = ChatSession(project, orchestrator)

    reply = run(chat.send("dibuja el flujo del login"))

    assert reply.text == "Aquí tienes el diagrama del login."
    assert reply.diagram["svg_path"] == svg
    assert chat.history[-1].diagram["mermaid"].startswith("flowchart")


def test_pilot_describes_itself_from_the_live_registries(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call("c1", "describe_runtime", topic="agents"),
            {"content": "Tengo estos agentes."},
        ],
    )
    reply = run(ChatSession(project, orchestrator).send("¿qué agentes tienes?"))

    assert reply.status == "ok"
    tool_message = provider.requests[1].messages[-1].content
    assert "python_coder" in tool_message
    assert "pilot" in tool_message
    # El planificador retirado no aparece aunque quede su id reservado.
    assert '"orchestrator"' not in tool_message


def test_describe_runtime_reports_the_model_behind_the_wrappers() -> None:
    from types import SimpleNamespace

    from satellite.tools.describe_runtime import describe

    inner = SimpleNamespace(model="deepseek-v4.1-flash")
    wrapped = SimpleNamespace(provider_name="commandcode", inner=inner)
    out = describe("model", SimpleNamespace(provider=wrapped))
    assert out == {"provider": "commandcode", "model": "deepseek-v4.1-flash"}


def test_without_the_pilot_agent_the_turn_fails_clearly(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, provider = _runtime(tmp_path, monkeypatch, [])
    factory = orchestrator.services.factory
    monkeypatch.setattr(factory, "can_create", lambda spec_id: spec_id != "pilot")

    reply = run(ChatSession(project, orchestrator).send("hola"))

    assert reply.status == "failed"
    assert "pilot" in reply.text
    assert provider.requests == []  # no hay otra ruta que llame al modelo


def _spec(agent_id: str, tools: list[str]) -> AgentSpec:
    return AgentSpec.from_dict(
        {
            "id": agent_id,
            "name": agent_id,
            "description": agent_id,
            "capabilities": ["a", "b"],
            "prompt_ref": agent_id,
            "tools": tools,
        }
    )


def test_user_created_agents_still_need_escalation_approval() -> None:
    pilot = _spec("pilot", ["read_file", "delegate"])
    custom = _spec("my_deployer", ["read_file", "run_command"])

    class Registry:
        def get(self, agent_id):
            return {"my_deployer": custom}[agent_id]

    class Factory:
        def create(self, agent_id):  # pragma: no cover - no debe llegar
            raise AssertionError("no se debe crear sin aprobación")

    manager = DelegationManager(Factory(), registry=Registry())
    result = run(
        manager.delegate(
            agent_id="my_deployer",
            task={"goal": "deploy"},
            parent_ctx=manager.root_context("t1"),
            project_root=Path("."),
            caller_spec=pilot,
        )
    )
    assert result.ok is False  # sin puerto de aprobación, se deniega


def test_ask_user_ends_the_turn_with_a_structured_question(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call(
                "c1",
                "ask_user",
                question="¿Qué validación quieres?",
                choices=[
                    {"id": "empty", "label": "Sólo vacías"},
                    {"id": "strong", "label": "Contraseña fuerte", "detail": "8+ caracteres"},
                ],
                recommended="strong",
            ),
            {"content": "no debería llamarse"},
        ],
    )
    chat = ChatSession(project, orchestrator)

    reply = run(chat.send("valida las contraseñas"))

    assert reply.status == "needs_user_input"
    assert len(provider.requests) == 1  # preguntar termina el turno
    assert reply.question == {
        "question": "¿Qué validación quieres?",
        "choices": [
            {"id": "empty", "label": "Sólo vacías", "detail": ""},
            {"id": "strong", "label": "Contraseña fuerte", "detail": "8+ caracteres"},
        ],
        "recommended": "strong",
    }
    assert "1. **Sólo vacías**" in reply.text
    assert "2. **Contraseña fuerte** ★ — 8+ caracteres" in reply.text
    assert chat.pending_question()["recommended"] == "strong"


def test_a_short_answer_is_linked_to_the_open_question(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call(
                "c1",
                "ask_user",
                question="¿Cuál archivo?",
                choices=[{"id": "api", "label": "api/auth.py"}, {"id": "web", "label": "web/auth.py"}],
            ),
            {"content": "Perfecto, trabajo sobre web/auth.py."},
        ],
    )
    chat = ChatSession(project, orchestrator, persist=True)
    run(chat.send("arregla el auth"))

    # Tras recargar la conversación la pregunta sigue abierta.
    reloaded = ChatSession(project, orchestrator, persist=True)
    reloaded.load()
    reply = run(reloaded.send("el segundo"))

    assert reply.status == "ok"
    user_prompt = provider.requests[1].messages[-1].content
    task = json.loads(user_prompt.split("User message:\n", 1)[1].split("\n\nContext", 1)[0])
    assert task["goal"] == "el segundo"
    assert task["answering"]["question"] == "¿Cuál archivo?"
    assert task["answering"]["selected"]["id"] == "web"
    assert reloaded.pending_question() is None  # respondida


def test_plan_mode_proposes_options_without_delegating(
    tmp_path: Path, monkeypatch
) -> None:
    project, orchestrator, _events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call(
                "c1",
                "ask_user",
                question="¿Qué enfoque?",
                choices=[{"id": "min", "label": "Mínimo"}, {"id": "full", "label": "Completo"}],
                recommended="min",
            )
        ],
    )
    reply = run(ChatSession(project, orchestrator).send("añade login", plan_mode=True))

    tools = {tool.name for tool in provider.requests[0].tools}
    assert "delegate" not in tools and "ask_user" in tools
    assert "# DELEGATION" not in provider.requests[0].messages[0].content
    assert "Plan mode" in provider.requests[0].messages[-1].content
    assert reply.status == "needs_user_input"
    assert [c["id"] for c in reply.question["choices"]] == ["min", "full"]


def test_selected_choice_only_resolves_unambiguous_answers() -> None:
    from satellite.orchestration.chat import answer_context

    question = {
        "question": "q",
        "choices": [{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}],
        "recommended": "b",
    }
    assert answer_context(question, "2")["selected"]["id"] == "b"
    assert answer_context(question, "la segunda")["selected"]["id"] == "b"
    assert answer_context(question, "Opción 1.")["selected"]["id"] == "a"
    assert answer_context(question, "alpha")["selected"]["id"] == "a"
    assert answer_context(question, "la recomendada")["selected"]["id"] == "b"
    assert "selected" not in answer_context(question, "ninguna, mejor otra cosa")
    assert "selected" not in answer_context(question, "7")
    assert answer_context(None, "2") is None


# --------------------------------------------------- correcciones tras la evaluación


def test_role_providers_are_metered_and_proxied(tmp_path: Path, monkeypatch) -> None:
    from satellite.providers.metered import MeteredProvider
    from satellite.safe_proxy.interceptor import SanitizingProvider

    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "home"))
    project = tmp_path / "p"
    (project / ".satellite" / "config").mkdir(parents=True)
    (project / ".satellite" / "config" / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}),
        encoding="utf-8",
    )
    orchestrator, _bus, _store, _reg = runtime_builder.build_runtime(project)
    factory = orchestrator.services.factory
    for role in ("orchestrator", "agent", "context"):
        provider = factory._provider_for(role)
        assert isinstance(provider, (SanitizingProvider, MeteredProvider)), role
        inner = provider.inner if isinstance(provider, SanitizingProvider) else provider
        assert isinstance(inner, MeteredProvider), role


def test_old_tool_results_are_truncated() -> None:
    from satellite.agents.runtime import TRUNCATED_NOTE, _compact_tool_results
    from satellite.core.messages import Message, Role

    messages = [Message(role=Role.USER, content="q")] + [
        Message(role=Role.TOOL, content="x" * 2000, tool_call_id=f"c{i}") for i in range(5)
    ]
    out = _compact_tool_results(messages, 2)
    tools = [m.content for m in out if m.role is Role.TOOL]
    assert all(c.endswith(TRUNCATED_NOTE) for c in tools[:3])
    assert tools[3:] == ["x" * 2000, "x" * 2000]
    # Idempotente: recortar otra vez no vuelve a añadir la nota.
    assert _compact_tool_results(out, 2) == out


def test_exhausted_budget_forces_an_answer_and_tool_markup_is_retried(
    tmp_path: Path, monkeypatch
) -> None:
    from satellite.orchestration import pilot as pilot_module

    monkeypatch.setattr(pilot_module, "TOOL_ROUND_BUDGET", 1)
    project, orchestrator, _events, provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            _call("c1", "read_file", path="auth.py"),
            _call("c2", "read_file", path="auth.py"),
            {"content": '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="read_file">'},
            {"content": "login delega en check()."},
        ],
    )
    reply = run(ChatSession(project, orchestrator).send("¿cómo funciona el login?"))

    assert reply.text == "login delega en check()."
    synthesis = provider.requests[2]
    assert synthesis.tools in ((), None)
    assert "cannot call tools" in synthesis.messages[-1].content
    assert "cannot call tools" in provider.requests[3].messages[-1].content


def test_unrecoverable_tool_markup_is_stripped() -> None:
    from satellite.agents.runtime import _has_tool_markup, _strip_tool_markup

    text = 'Resumen útil.\n<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="x"> </｜｜DSML｜｜ calls>'
    assert _has_tool_markup(text)
    assert _strip_tool_markup(text) == "Resumen útil."


def test_turn_tokens_include_delegations(tmp_path: Path, monkeypatch) -> None:
    from satellite.core.messages import Usage  # noqa: F401 — documenta el formato

    coder = {
        "summary": "ok",
        "files_changed": [],
        "verification": {"command": "pytest", "passed": True, "details": ""},
    }
    project, orchestrator, _events, _provider = _runtime(
        tmp_path,
        monkeypatch,
        [
            {**_call("c1", "delegate", agent_id="python_coder", task={"goal": "x"}),
             "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}},
            {"content": json.dumps(coder),
             "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050}},
            {"content": "Hecho.",
             "usage": {"prompt_tokens": 200, "completion_tokens": 20, "total_tokens": 220}},
        ],
    )
    reply = run(ChatSession(project, orchestrator).send("haz x"))
    assert reply.metrics["tokens_in"] == 100 + 1000 + 200
    assert reply.metrics["tokens_out"] == 10 + 50 + 20


def test_read_file_reads_a_line_range(tmp_path: Path) -> None:
    from satellite.tools.base import ToolContext
    from satellite.tools.fs import ReadFileTool

    (tmp_path / "a.py").write_text("\n".join(f"l{i}" for i in range(1, 11)), encoding="utf-8")
    ctx = ToolContext(project_root=tmp_path)
    result = run(ReadFileTool().execute({"path": "a.py", "start_line": 3, "end_line": 4}, ctx))
    assert result.output == "[líneas 3-4 de 10]\n3: l3\n4: l4"
    whole = run(ReadFileTool().execute({"path": "a.py"}, ctx))
    assert whole.output.startswith("l1\nl2")
    beyond = run(ReadFileTool().execute({"path": "a.py", "start_line": 50}, ctx))
    assert beyond.success is False


def test_search_files_finds_text_inside_files(tmp_path: Path) -> None:
    from satellite.tools.base import ToolContext
    from satellite.tools.primitives import SearchFilesTool

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\nERROR = 'no provider'\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.py").write_text("ERROR = 'no provider'\n", encoding="utf-8")
    (tmp_path / "bin.py").write_bytes(b"\x00\x01no provider")
    ctx = ToolContext(project_root=tmp_path)
    tool = SearchFilesTool()

    found = run(tool.execute({"pattern": "**/*.py", "content": "No Provider", "ignore_case": True}, ctx))
    assert found.success is True
    assert found.output["matches"] == [{"path": "pkg/a.py", "line": 2, "text": "ERROR = 'no provider'"}]

    regex = run(tool.execute({"pattern": "**/*.py", "content": r"^x\s*=", "regex": True}, ctx))
    assert [m["line"] for m in regex.output["matches"]] == [1]

    bad = run(tool.execute({"pattern": "**/*.py", "content": "(", "regex": True}, ctx))
    assert bad.success is False and bad.error_code == "invalid_arguments"

    listing = run(tool.execute({"pattern": "**/*.py"}, ctx))
    assert isinstance(listing.output, list)


def test_language_agents_are_told_to_search_and_test_narrowly() -> None:
    prompts = Path(__file__).resolve().parent.parent / "satellite" / "agents" / "prompts"
    agents = sorted(prompts.glob("*_coder.system.txt")) + sorted(prompts.glob("*_auditor.system.txt"))
    assert len(agents) >= 20
    for prompt in agents:
        text = prompt.read_text(encoding="utf-8")
        assert "# WORKING EFFICIENTLY" in text and "`content` searches text" in text, prompt.name
