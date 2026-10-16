"""Contexto que aporta el usuario: menciones ``@`` e instrucciones del proyecto."""

import asyncio
import json
from pathlib import Path

from satellite.agents.base import Agent, AgentContext, AgentFactory
from satellite.context.instructions import (
    find_instructions,
    instructions_ref,
    load_instructions,
)
from satellite.context.mentions import MAX_MENTIONS, parse, user_refs
from satellite.context.resolver import ContextResolver
from satellite.core.context_models import ContextRef, ContextRefKind, ContextLimits
from satellite.core.graph import ExecutionGraph, Node
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.task import Task, TaskResult, TaskStatus
from satellite.orchestration.base import RuntimeServices
from satellite.orchestration.chat import ChatSession
from satellite.orchestration.runner import DefaultGraphRunner


def run(coro):
    return asyncio.run(coro)


# --- menciones ---


def test_a_file_mention_becomes_a_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text("x", encoding="utf-8")

    result = parse("mira @src/api.py y dime", tmp_path)

    assert result.refs == (ContextRef(ContextRefKind.PATH, "src/api.py"),)
    assert result.missing == ()
    assert bool(result) is True


def test_a_directory_and_a_pattern_are_understood(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    result = parse("revisa @src y @**/*.py", tmp_path)

    assert [ref.kind for ref in result.refs] == [
        ContextRefKind.DIR,
        ContextRefKind.PATTERN,
    ]


def test_a_bare_name_is_a_symbol(tmp_path: Path) -> None:
    result = parse("¿dónde se define @login?", tmp_path)

    assert result.refs == (ContextRef(ContextRefKind.SYMBOL, "login"),)


def test_a_dotted_token_without_a_separator_is_a_symbol(tmp_path: Path) -> None:
    assert parse("@objeto.metodo", tmp_path).refs == (
        ContextRef(ContextRefKind.SYMBOL, "objeto.metodo"),
    )


def test_a_missing_file_is_reported_not_invented(tmp_path: Path) -> None:
    """Lo que parece un fichero y no está se dice; no se manda contexto vacío."""
    result = parse("mira @src/no_existe.py", tmp_path)

    assert result.refs == ()
    assert result.missing == ("src/no_existe.py",)
    assert "no encontrado" in result.summary()


def test_punctuation_and_repeats_are_handled(tmp_path: Path) -> None:
    result = parse("@a.py, y otra vez @a.py. gracias", tmp_path)

    assert result.refs == ()  # a.py no existe: se reporta una sola vez
    assert result.missing == ("a.py",)


def test_the_cap_keeps_the_prompt_bounded(tmp_path: Path) -> None:
    texto = " ".join(f"@sym{i}" for i in range(MAX_MENTIONS + 5))

    result = parse(texto, tmp_path)

    assert len(result.refs) == MAX_MENTIONS


def test_user_refs_reads_the_task_metadata(tmp_path: Path) -> None:
    task = Task(
        goal="x",
        project_root=tmp_path,
        metadata={"context_refs": [{"kind": "path", "value": "a.py"}]},
    )
    assert user_refs(task) == (ContextRef(ContextRefKind.PATH, "a.py"),)


def test_user_refs_ignores_garbage(tmp_path: Path) -> None:
    task = Task(
        goal="x",
        project_root=tmp_path,
        metadata={"context_refs": [{"kind": "no-existe", "value": "a"}, "basura"]},
    )
    assert user_refs(task) == ()


# --- instrucciones del proyecto ---


def test_agents_md_is_the_default_and_wins(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Sé breve.\n", encoding="utf-8")
    (tmp_path / "SATELLITE.md").write_text("Otra cosa.\n", encoding="utf-8")

    assert find_instructions(tmp_path).name == "AGENTS.md"
    nombre, texto = load_instructions(tmp_path)
    assert nombre == "AGENTS.md" and "Sé breve" in texto


def test_satellite_md_is_used_when_agents_md_is_missing(tmp_path: Path) -> None:
    (tmp_path / "SATELLITE.md").write_text("Reglas.\n", encoding="utf-8")

    assert load_instructions(tmp_path)[0] == "SATELLITE.md"


def test_without_instructions_there_is_nothing(tmp_path: Path) -> None:
    assert load_instructions(tmp_path) == ("", "")
    assert instructions_ref(Task(goal="x", project_root=tmp_path)) == ()


def test_long_instructions_are_truncated_with_a_notice(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 9_000, encoding="utf-8")

    _nombre, texto = load_instructions(tmp_path)

    assert "truncado" in texto


def test_the_instructions_ref_points_at_the_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Reglas.\n", encoding="utf-8")

    refs = instructions_ref(Task(goal="x", project_root=tmp_path))

    assert len(refs) == 1 and refs[0].kind is ContextRefKind.PROJECT_RULES
    assert "AGENTS.md" in refs[0].value


# --- el resolver ---


def test_a_symbol_returns_every_file_not_only_the_first(tmp_path: Path) -> None:
    """Antes se cortaba en la primera coincidencia del primer fichero."""
    (tmp_path / "a.py").write_text("def autenticar():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("autenticar()\n", encoding="utf-8")
    resolver = ContextResolver(tmp_path)
    ref = ContextRef(ContextRefKind.SYMBOL, "autenticar")

    section = run(resolver.resolve_ref(ref))

    assert section is not None
    assert "a.py" in section.content and "b.py" in section.content


def test_a_symbol_says_how_many_hits_it_left_out(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "\n".join("autenticar()" for _ in range(40)), encoding="utf-8"
    )
    resolver = ContextResolver(tmp_path)
    ref = ContextRef(ContextRefKind.SYMBOL, "autenticar")

    section = run(resolver.resolve_ref(ref, ContextLimits(max_chars_per_section=1_000)))

    assert section is not None
    assert "coincidencia(s) más" in section.content
    assert section.chars <= 1_000


def test_the_project_rules_are_resolved_as_a_section(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "No toques la carpeta de producción.\n", encoding="utf-8"
    )
    resolver = ContextResolver(tmp_path)
    ref = ContextRef(ContextRefKind.PROJECT_RULES, "reglas")

    section = run(resolver.resolve_ref(ref))

    assert section is not None
    assert "No toques la carpeta de producción" in section.content


def test_without_instructions_the_rules_section_is_none(tmp_path: Path) -> None:
    resolver = ContextResolver(tmp_path)
    ref = ContextRef(ContextRefKind.PROJECT_RULES, "reglas")

    assert run(resolver.resolve_ref(ref)) is None


# --- el runner ---


class _MotorFalso:
    """Motor de contexto de mentira: graba las refs y contesta una sección por ref."""

    def __init__(self) -> None:
        self.refs: tuple = ()

    async def resolve(self, refs):
        from satellite.core.context_models import ContextBundle, ContextSection

        self.refs = tuple(refs)
        return ContextBundle(
            sections=tuple(
                ContextSection(ref=ref, content=f"contenido de {ref.value}") for ref in refs
            )
        )


class _AgenteFalso(Agent):
    def __init__(self, spec_id: str) -> None:
        self.spec_id = spec_id
        self.last_ctx: AgentContext | None = None

    async def execute(self, task: dict, ctx: AgentContext) -> AgentResult:
        self.last_ctx = ctx
        return AgentResult(
            node_id=ctx.node_id, spec_id=self.spec_id, status=AgentStatus.OK, summary="ok"
        )


class _FactoryFalsa(AgentFactory):
    def __init__(self) -> None:
        self.agents: dict[str, _AgenteFalso] = {}

    def create(self, spec_id: str, *, model_role: str | None = None) -> Agent:
        self.agents.setdefault(spec_id, _AgenteFalso(spec_id))
        return self.agents[spec_id]


def _one_node_graph() -> ExecutionGraph:
    graph = ExecutionGraph(entry_node="n1")
    graph.add_node(Node(id="n1", spec_id="sA"))
    return graph


def test_the_node_gets_the_mentions_and_the_project_rules(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Sé breve.\n", encoding="utf-8")
    engine = _MotorFalso()
    factory = _FactoryFalsa()
    task = Task(
        goal="@a.py ¿qué hace?",
        project_root=tmp_path,
        metadata={"context_refs": [{"kind": "path", "value": "a.py"}]},
    )

    result = run(
        DefaultGraphRunner(factory).run(
            _one_node_graph(), task, RuntimeServices(factory=factory, context=engine)
        )
    )

    assert result.status is TaskStatus.OK
    kinds = [ref.kind for ref in engine.refs]
    assert ContextRefKind.PATH in kinds
    assert ContextRefKind.PROJECT_RULES in kinds
    secciones = factory.agents["sA"].last_ctx.context.sections
    assert any(section.ref.kind is ContextRefKind.PROJECT_RULES for section in secciones)
    assert any("a.py" in section.content for section in secciones)


def test_without_mentions_or_rules_the_refs_are_only_the_node_ones(tmp_path: Path) -> None:
    engine = _MotorFalso()
    factory = _FactoryFalsa()

    run(
        DefaultGraphRunner(factory).run(
            _one_node_graph(),
            Task(goal="hola", project_root=tmp_path),
            RuntimeServices(factory=factory, context=engine),
        )
    )

    assert engine.refs == ()


# --- la conversación ---


class _OrquestadorGrabador:
    """Orquestador de mentira: se queda con la tarea y responde ok."""

    def __init__(self) -> None:
        self.tasks: list = []

    async def execute(self, task):
        self.tasks.append(task)
        return TaskResult(task_id=task.task_id, status=TaskStatus.OK, summary="ok")


def test_sending_a_mention_attaches_the_context_to_the_task(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    orchestrator = _OrquestadorGrabador()
    chat = ChatSession(tmp_path, orchestrator, persist=False)

    run(chat.send("mira @a.py y @no_existe.py"))

    metadata = orchestrator.tasks[0].metadata
    assert metadata["context_refs"] == [{"kind": "path", "value": "a.py"}]
    assert metadata["context_missing"] == ["no_existe.py"]


def test_a_message_without_mentions_carries_no_context(tmp_path: Path) -> None:
    orchestrator = _OrquestadorGrabador()
    chat = ChatSession(tmp_path, orchestrator, persist=False)

    run(chat.send("hola"))

    assert "context_refs" not in orchestrator.tasks[0].metadata


# --- la TUI ---


def make_app(tmp_path: Path, monkeypatch):
    from satellite.tui.app import SatelliteTUI
    from satellite.tui.session import Session

    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    monkeypatch.delenv("SATELLITE_SKILLS_DIR", raising=False)
    cfg_dir = tmp_path / ".satellite" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"name": "fake", "model": "m"}), encoding="utf-8"
    )
    return SatelliteTUI(Session(tmp_path, home=home))


def test_the_help_teaches_the_mentions() -> None:
    from satellite.tui import palette

    assert "@" in palette.help_text()


def test_the_tui_announces_the_instructions_and_the_mentions(tmp_path, monkeypatch) -> None:
    async def scenario():
        (tmp_path / "AGENTS.md").write_text("Sé breve.\n", encoding="utf-8")
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        app = make_app(tmp_path, monkeypatch)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "instrucciones=AGENTS.md" in "\n".join(app.transcript)

            await app._do_chat("@a.py ¿qué hace?")
            await pilot.pause()
            assert "contexto: a.py (path)" in "\n".join(app.transcript)

    asyncio.run(scenario())
