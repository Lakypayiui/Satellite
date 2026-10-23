"""Transversales del prompt caching (F7): la cadena completa, no unidades sueltas.

Cubre, con las piezas reales enganchadas entre sí: prefijo estable del piloto en
multi-turno simulado (aciertos de cache), rechazo de contenido dinámico en el system, deriva
vs cambio legítimo, costes con precios reales y la distinción
"no cacheado" / "unknown".
"""

import asyncio
import json
from pathlib import Path

from types import SimpleNamespace

from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.runtime import LLMAgent, build_system_prompt
from satellite.core.messages import CallContext, LLMRequest, Message, Role
from satellite.core.spec import AgentSpec
from satellite.core.task import Task
from satellite.metrics.cache_report import build_cache_report
from satellite.metrics.pricing import ModelPrice, PriceBook
from satellite.metrics.stats import build_report
from satellite.observability.call_log import (
    CACHED,
    NOT_CACHED,
    UNKNOWN,
    WRITTEN,
    CallRecorder,
)
from satellite.orchestration.pilot import PilotOrchestrator
from satellite.providers.caching import TTL_1H, TTL_5M, TtlLedger
from satellite.providers.fake import FakeLLM
from satellite.providers.metered import MeteredProvider
from satellite.providers.prefix import prefix_payload

PROMPTS = Path(__file__).resolve().parent.parent / "satellite" / "agents" / "prompts"
SYSTEM_PROMPT = "# ROL\nEres el piloto.\nSALIDA:\n{output_schema}"

DRIFT = "drift"
CATALOG_CHANGED = "catalog_changed"


def run(coro):
    return asyncio.run(coro)


def make_spec(spec_id: str, *, capabilities=("do_thing",)) -> AgentSpec:
    return AgentSpec(
        id=spec_id,
        name=spec_id.replace("_", " ").title(),
        description=f"hace {spec_id}",
        capabilities=capabilities,
        prompt_ref=spec_id,
        tools=("read_file",),
    )


class _PilotFactory:
    """Crea el piloto real (prompts del paquete) hablando con ``provider``."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.prompts = DefaultPromptRegistry()
        self.prompts.load_dir(PROMPTS)

    def create(self, spec_id, *, model_role=None):
        spec = AgentSpec(
            id=spec_id,
            name="Pilot",
            capabilities=("converse",),
            prompt_ref=spec_id,
            model_role="orchestrator",
        )
        return LLMAgent(
            spec=spec,
            system_prompt=self.prompts.get_system(spec_id),
            user_template=self.prompts.get_user(spec_id),
            tools=[],
            provider=self.provider,
        )


def pilot_style_request(
    tmp_path: Path,
    *,
    goal: str,
    history: list[dict] | None = None,
    specs: list[AgentSpec],
    task_id: str = "t1",
) -> LLMRequest:
    """La petición que manda el piloto real en un turno (capturada del proveedor)."""
    registry = DefaultAgentRegistry()
    for spec in specs:
        registry.register_spec(spec)
    capture = FakeLLM(default_content="ok")
    services = SimpleNamespace(registry=registry, factory=_PilotFactory(capture))
    task = Task(
        goal=goal, project_root=tmp_path, task_id=task_id, metadata={"history": history or []}
    )
    run(PilotOrchestrator(services).execute(task))
    return capture.requests[0]


# ---------------------------------------------------------------- multi-turno


def test_multi_turn_simulated_cache_hits(tmp_path: Path) -> None:
    """Turno 2 con el mismo prefijo estable acierta cache (y se nota en la métrica)."""
    recorder = CallRecorder(tmp_path)
    inner = FakeLLM(
        script=[
            {
                "content": "{}",
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 20,
                    "total_tokens": 1220,
                    "cache_creation_input_tokens": 1100,
                    "cache_read_input_tokens": 0,
                },
            },
            {
                "content": "{}",
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 20,
                    "total_tokens": 1220,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 1100,
                },
            },
        ]
    )
    book = PriceBook({"m": ModelPrice(input=3.0, output=15.0, cache_write=3.75, cache_read=0.3)})
    provider = MeteredProvider(inner, recorder, model="m", price_book=book)
    specs = [make_spec("alpha_agent")]

    project = tmp_path / "project"
    project.mkdir()
    run(provider.complete(pilot_style_request(project, goal="primer mensaje", specs=specs)))
    run(
        provider.complete(
            pilot_style_request(
                project,
                goal="segunda cosa",
                history=[
                    {"role": "user", "content": "primer mensaje"},
                    {"role": "assistant", "content": "ok"},
                ],
                specs=specs,
            )
        )
    )

    records = recorder.load()
    assert len(records) == 2
    # el prefijo estable NO cambia aunque cambie la conversación
    assert records[0].prefix_hash == records[1].prefix_hash
    assert records[0].cache_status == WRITTEN
    assert records[1].cache_status == CACHED
    assert records[1].kind == ""  # y eso no es deriva

    report = build_cache_report(records, book)
    # el hit rate es agregado: cache_read / input total (1200 + 1200)
    assert report.hit_rate == round(1100 / 2400, 4)
    assert report.statuses == {WRITTEN: 1, CACHED: 1}
    assert report.real_cost is not None and report.hypothetical_cost is not None
    assert report.real_cost < report.hypothetical_cost
    assert report.savings is not None and report.savings > 0
    assert "hit rate: 45.8%" in report.format()
    assert "deriva (CACHE_PREFIX_DRIFT): 0" in report.format()


def test_system_never_carries_dynamic_content(tmp_path: Path) -> None:
    """El system del piloto es estático: su catálogo de delegación sí, la tarea no."""
    request = pilot_style_request(
        tmp_path,
        goal="TAREA-SECRETA",
        history=[{"role": "user", "content": "HISTORIAL-SECRETO"}],
        specs=[make_spec("alpha_agent")],
    )
    system = request.messages[0]
    assert system.role is Role.SYSTEM
    assert "TAREA-SECRETA" not in system.content
    assert "HISTORIAL-SECRETO" not in system.content
    # el catálogo (fijo durante el turno) va en el system, ordenado y sin el piloto
    assert "# DELEGATION" in system.content
    assert "- alpha_agent:" in system.content

    # sólo system + mensaje dinámico: no hay bloque USER estable
    assert request.stable_user_count == 0
    assert [message.role for message in request.messages] == [Role.SYSTEM, Role.USER]

    dynamic = request.messages[-1]
    assert "TAREA-SECRETA" in dynamic.content
    assert "HISTORIAL-SECRETO" in dynamic.content


def stable_bytes(request: LLMRequest) -> str:
    """Payload del prefijo estable de la petición (tools + system; catálogo en el system)."""
    return prefix_payload(request.tools, request.messages[0].content, "")


def test_stable_prefix_ignores_history_and_task(tmp_path: Path) -> None:
    """Los bytes del prefijo estable sólo dependen de tools+system+catálogo."""
    specs = [make_spec("alpha_agent"), make_spec("beta_agent")]
    first = pilot_style_request(tmp_path, goal="uno", specs=specs)
    second = pilot_style_request(
        tmp_path,
        goal="dos distinto", history=[{"role": "user", "content": "hola"}], specs=specs
    )
    assert stable_bytes(first) == stable_bytes(second)
    # …pero el mensaje dinámico sí cambia
    assert first.messages[-1].content != second.messages[-1].content


def test_built_system_has_no_dynamic_placeholders() -> None:
    system = build_system_prompt(SYSTEM_PROMPT, output_schema={"type": "object"})
    assert "{task}" not in system
    assert "{context}" not in system
    assert "{feedback}" not in system
    assert "{output_schema}" not in system


# ---------------------------------------------------------------- deriva


def test_drift_vs_legitimate_change(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    provider = MeteredProvider(
        FakeLLM(script=[{"content": "{}"}] * 4), recorder, model="m"
    )
    specs = [make_spec("alpha_agent")]
    project = tmp_path / "project"
    project.mkdir()

    # 1-2: mismo prefijo (sólo cambia lo dinámico)
    run(provider.complete(pilot_style_request(project, goal="uno", specs=specs)))
    run(provider.complete(pilot_style_request(project, goal="dos", specs=specs)))
    # 3: cambia el SYSTEM -> misma spec/catálogo/tools/modelo => deriva
    provider.inner = FakeLLM(script=[{"content": "{}"}] * 2)
    request = pilot_style_request(project, goal="tres", specs=specs)
    changed_system = LLMRequest(
        messages=(
            Message(role=Role.SYSTEM, content=request.messages[0].content + "\n# EXTRA"),
            *request.messages[1:],
        ),
        stable_user_count=request.stable_user_count,
        call=request.call,
    )
    run(provider.complete(changed_system))

    kinds = [record.kind for record in recorder.load()]
    assert kinds == ["", "", DRIFT]
    report = build_cache_report(recorder.load())
    assert report.drift_events == 1


def test_pilot_catalog_change_is_not_drift(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    provider = MeteredProvider(
        FakeLLM(script=[{"content": "{}"}] * 2), recorder, model="m"
    )
    specs = [make_spec("alpha_agent")]
    project = tmp_path / "project"
    project.mkdir()

    run(provider.complete(pilot_style_request(project, goal="uno", specs=specs)))
    # cambia el CATÁLOGO (spec nueva) => cambio legítimo, no deriva
    run(
        provider.complete(
            pilot_style_request(project, goal="dos", specs=specs + [make_spec("beta_agent")])
        )
    )

    kinds = [record.kind for record in recorder.load()]
    assert kinds == ["", CATALOG_CHANGED]


# ---------------------------------------------------------------- costes


def test_costs_and_unknown_are_reported_honestly(tmp_path: Path) -> None:
    book = PriceBook({"m": ModelPrice(input=10.0, output=30.0, cache_write=12.5, cache_read=1.0)})
    records = [
        # con cache
        *recorder_records(tmp_path, cache_creation=0, cache_read=500_000, input_tokens=1_000_000),
        # sin precios del modelo -> no entra en el coste
        *recorder_records(tmp_path, model="otro", cache_creation=0, cache_read=0),
    ]
    priced = build_cache_report(records, book)
    # 500k a precio pleno + 500k leídos de cache + 10 de salida
    expected_real = (500_000 * 10.0 + 500_000 * 1.0 + 10 * 30.0) / 1_000_000
    assert priced.real_cost == expected_real
    # sin cache, todo el input a precio pleno
    assert priced.hypothetical_cost == (1_000_000 * 10.0 + 10 * 30.0) / 1_000_000
    assert priced.savings is not None and priced.savings > 0
    assert priced.priced_calls == 1  # el modelo 'otro' no tiene precio

    without = build_cache_report(records)
    assert without.real_cost is None
    assert "unknown" in without.format()


def recorder_records(tmp_path: Path, **overrides) -> list:
    """Ejecuta una llamada real con el uso indicado y devuelve sus registros."""
    recorder = CallRecorder(tmp_path / f"call{abs(hash(tuple(sorted(overrides.items()))))}")
    usage = {
        "prompt_tokens": overrides.get("input_tokens", 100),
        "completion_tokens": 10,
        "total_tokens": overrides.get("input_tokens", 100) + 10,
        "cache_creation_input_tokens": overrides.get("cache_creation"),
        "cache_read_input_tokens": overrides.get("cache_read"),
    }
    provider = MeteredProvider(
        FakeLLM(script=[{"content": "{}", "usage": usage}]),
        recorder,
        model=overrides.get("model", "m"),
    )
    run(
        provider.complete(
            LLMRequest(
                messages=(Message(role=Role.USER, content="hola"),),
                call=CallContext(task_id="t", role="agent"),
            )
        )
    )
    return recorder.load()


def test_no_cached_and_unknown_are_distinguished(tmp_path: Path) -> None:
    recorder = CallRecorder(tmp_path)
    provider = MeteredProvider(
        FakeLLM(
            script=[
                {
                    "content": "{}",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "total_tokens": 11,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
                {"content": "{}"},  # sin usage: el proveedor no lo reporta
            ]
        ),
        recorder,
        model="m",
    )
    project = tmp_path / "project"
    project.mkdir()
    request = pilot_style_request(project, goal="x", specs=[make_spec("alpha_agent")])
    run(provider.complete(request))
    run(provider.complete(request))

    records = recorder.load()
    assert [record.cache_status for record in records] == [NOT_CACHED, UNKNOWN]
    report = build_cache_report(records)
    assert report.statuses == {NOT_CACHED: 1, UNKNOWN: 1}
    assert report.cache_read == 0  # reportado y cero
    assert report.unknown_calls == 1
    assert report.hit_rate == 0.0


def test_skill_savings_use_the_real_cache_cost(tmp_path: Path) -> None:
    """[AJ-8] de las llamadas sale el coste real por uso que alimenta el informe."""
    book = PriceBook({"m": ModelPrice(input=10.0, output=0.0, cache_write=12.5, cache_read=1.0)})
    records = recorder_records(tmp_path, cache_creation=0, cache_read=800, input_tokens=1000)
    cache = build_cache_report(records, book)
    # input a precio pleno (1000-800) + salida (10): lo leído de cache no se cuenta
    assert cache.real_tokens_per_call == 210.0

    report = build_report(
        [
            {
                "id": "mi_skill",
                "stats": {
                    "uses": 5,
                    "successes": 5,
                    "first_resolution_tokens": 1000,
                    "creation_tokens": 3000,
                },
            }
        ],
        call_cost_tokens=int(cache.real_tokens_per_call or 0),
        cache=cache.to_dict(),
    )
    assert report.skills[0].call_cost_tokens == 210
    assert report.skills[0].savings == 5 * (1000 - 210) - 3000
    assert report.to_dict()["cache"]["hit_rate"] == 0.8


# ---------------------------------------------------------------- TTL


def test_mixed_ttl_rules_hold_together() -> None:
    ledger = TtlLedger()
    assert ledger.effective("prefijo", TTL_1H) == (TTL_1H, False)
    # pedir 5m sobre un prefijo que ya está a 1h NO lo acorta: se degrada la petición
    assert ledger.effective("prefijo", TTL_5M) == (TTL_1H, True)
    # subir de 5m a 1h sí es válido
    assert ledger.effective("otro", TTL_5M) == (TTL_5M, False)
    assert ledger.effective("otro", TTL_1H) == (TTL_1H, False)
    # olvidar el prefijo permite volver a un TTL corto
    ledger.forget("prefijo")
    assert ledger.effective("prefijo", TTL_5M) == (TTL_5M, False)


def test_report_serialises_for_the_api(tmp_path: Path) -> None:
    records = recorder_records(tmp_path, cache_creation=0, cache_read=10, input_tokens=100)
    payload = json.loads(json.dumps(build_cache_report(records).to_dict()))
    assert payload["cache_read"] == 10
    assert payload["statuses"][CACHED] == 1
