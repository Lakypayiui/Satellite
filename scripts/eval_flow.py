"""Evaluación del flujo piloto contra un proveedor real (docs/PLAN-flujo-piloto.md, fase 7).

Ejecuta una serie de conversaciones, cada caso sobre su propia **copia** del proyecto, y
registra, por caso, qué tools y delegaciones usó el piloto, si preguntó, la latencia hasta el primer texto visible y los tokens. Cada caso
declara expectativas blandas (p. ej. «debe leer antes de responder») y el informe dice
cuáles se cumplieron.

No entra en CI: usa el proveedor configurado (``satellite provider``), gasta tokens y
envía contenido del proyecto a ese proveedor. Los permisos (rutas fuera del proyecto,
comandos) se **deniegan** siempre: la evaluación mide cómo decide el turno, no ejecuta
comandos arbitrarios. Las ediciones de los agentes delegados ocurren sólo en la copia.

Uso:

    .venv/bin/python scripts/eval_flow.py                  # el proyecto actual
    .venv/bin/python scripts/eval_flow.py --project ../otro --cases casos.json
    .venv/bin/python scripts/eval_flow.py --only saludo,web

Formato de ``--cases`` (lista JSON): ``{"id", "turns": [str, ...], "expect": {...}}`` con
``expect`` opcional: ``tools_any`` (alguna de estas tools), ``no_tools`` (true),
``uses_graph`` (el turno emitió un grafo),
``delegate`` (agente), ``asks`` (true: termina en pregunta) y ``selected`` (true: el
último turno resolvió una opción de la pregunta anterior).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from satellite.approval.base import DenyAllApprovalPort  # noqa: E402
from satellite.cli.runtime_builder import build_runtime  # noqa: E402
from satellite.core.events import EventType  # noqa: E402
from satellite.orchestration.chat import ChatSession  # noqa: E402

READ_TOOLS = ["read_file", "search_files", "list_dir", "lsp"]

DEFAULT_CASES: list[dict] = [
    {"id": "saludo", "turns": ["hola"], "expect": {"no_tools": True}},
    {
        "id": "pregunta-codigo",
        "turns": ["¿Dónde se valida la configuración del proveedor y qué pasa si es inválida?"],
        "expect": {"tools_any": READ_TOOLS},
    },
    {
        "id": "modelo-de-datos",
        "turns": ["¿qué modelo de datos usa el proyecto?"],
        "expect": {"tools_any": READ_TOOLS},
    },
    {
        "id": "abre-config",
        "turns": ["abre el archivo de configuración y dime qué proveedor está activo"],
        "expect": {"tools_any": READ_TOOLS},
    },
    {
        "id": "arquitectura",
        "turns": ["explícame la arquitectura del orquestador"],
        "expect": {"tools_any": READ_TOOLS},
    },
    {
        "id": "aclaracion",
        "turns": [
            "Quiero cambiar un valor por defecto de la configuración, pero hay varios. "
            "Pregúntame cuál con opciones antes de tocar nada.",
            "el primero",
        ],
        "expect": {"asks_first": True, "selected": True},
    },
    {
        "id": "web",
        "turns": ["busca en internet cuál es la última versión estable de Textual"],
        "expect": {"delegate": "web_researcher"},
    },
    {
        "id": "diagrama",
        "turns": ["haz un diagrama del flujo de aprobación de permisos"],
        "expect": {"delegate": "pele_diagram_agent"},
    },
    {
        "id": "cambio",
        "turns": [
            "añade un test que compruebe que load_settings devuelve policy_mode 'ask' "
            "cuando el archivo de configuración no existe"
        ],
        "expect": {"delegate_any": ["python_coder", "python_auditor"]},
    },
    {
        "id": "grafo",
        "turns": [
            "Tres tareas independientes, en archivos distintos que no se tocan entre sí: "
            "1) añade o mejora el docstring de módulo de satellite/events/base.py; "
            "2) añade o mejora el docstring de módulo de satellite/events/bus.py; "
            "3) añade o mejora el docstring de módulo de satellite/core/errors.py. "
            "Hazlas a la vez."
        ],
        "expect": {"uses_graph": True},
    },
    {
        "id": "hazlo",
        "turns": [
            "propón en una frase cómo mejorarías el mensaje de error cuando falta el "
            "proveedor, sin cambiar nada todavía",
            "hazlo",
        ],
        "expect": {"delegate_any": ["python_coder"]},
    },
]

IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "node_modules", "dist", "dist-electron", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "*.egg-info",
)


def copy_project(source: Path) -> Path:
    """Copia el proyecto a un directorio temporal y fuerza el modo piloto."""
    target = Path(tempfile.mkdtemp(prefix="satellite-eval-")) / source.name
    shutil.copytree(source, target, ignore=IGNORE)
    return target


async def run_case(project: Path, case: dict) -> dict:
    orchestrator, bus, _store, _registry = build_runtime(project, allow_desktop=False)
    chat = ChatSession(project, orchestrator)
    approval = DenyAllApprovalPort()
    turns_report = []
    for text in case["turns"]:
        events: list = []
        bus.subscribe(events.append)
        started = time.perf_counter()
        first_text: float | None = None

        def mark(event) -> None:
            nonlocal first_text
            if event.event_type is EventType.TEXT_DELTA and first_text is None:
                first_text = time.perf_counter()

        bus.subscribe(mark)
        calls_before = len(read_calls(project))
        answering = chat.pending_question()
        reply = await chat.send(text, approval=approval)
        elapsed = time.perf_counter() - started
        # El resumen del turno se guarda en segundo plano: se espera aquí para que sus
        # llamadas cuenten en este turno y no en el siguiente.
        await chat.flush_background()
        bus.unsubscribe(events.append)
        bus.unsubscribe(mark)
        pilot_tools = [
            e.tool_name for e in events
            if e.event_type is EventType.TOOL_CALLED and e.node_id == "pilot"
        ]
        # Traza de los agentes delegados: qué tool, con qué argumentos y si salió bien.
        agent_tools = [
            {
                "agent": e.node_id,
                "tool": e.tool_name,
                "args": _short_args(e.payload.get("arguments")),
                "ok": bool(e.payload.get("success", True)),
            }
            for e in events
            if e.event_type is EventType.TOOL_CALLED and e.node_id not in (None, "pilot")
        ]
        delegations = [
            e.payload.get("agent_id") for e in events
            if e.event_type is EventType.DELEGATION_STARTED
        ]
        used_graph = any(e.event_type is EventType.GRAPH_BUILT for e in events)
        selected = None
        if answering:
            from satellite.orchestration.chat import answer_context

            selected = (answer_context(answering, text) or {}).get("selected")
        calls = read_calls(project)[calls_before:]
        turns_report.append(
            {
                # Consumo real de todas las llamadas del turno (piloto y delegados),
                # según el registro de llamadas medidas.
                "llm_calls": len(calls),
                "llm_in": sum(int(c.get("input_tokens") or 0) for c in calls),
                "llm_out": sum(int(c.get("output_tokens") or 0) for c in calls),
                "cache_read": sum(int(c.get("cache_read") or 0) for c in calls),
                "text": text,
                "status": reply.status,
                "reply": reply.text[:400],
                "pilot_tools": pilot_tools,
                "agent_tools": agent_tools,
                "delegations": delegations,
                "asked": reply.question is not None,
                "selected": selected,
                "graph": used_graph,
                "first_text_s": round(first_text - started, 2) if first_text else None,
                "total_s": round(elapsed, 2),
                "tokens_in": int((reply.metrics or {}).get("tokens_in", 0) or 0),
                "tokens_out": int((reply.metrics or {}).get("tokens_out", 0) or 0),
            }
        )
    return {"id": case["id"], "turns": turns_report, "checks": check(case, turns_report)}


def _short_args(arguments) -> str:
    """Argumentos de una tool en una línea corta (ruta, rango, acción, patrón…)."""
    if not isinstance(arguments, dict):
        return ""
    keys = ("path", "start_line", "end_line", "action", "pattern", "content", "query", "command", "symbol")
    parts = [f"{key}={arguments[key]}" for key in keys if arguments.get(key) not in (None, "")]
    text = " ".join(str(part) for part in parts)
    return text if len(text) <= 120 else text[:119] + "…"


def tool_summary(results: list[dict]) -> dict[str, dict[str, int]]:
    """Uso de tools por agente delegado, sumado en todos los casos."""
    out: dict[str, dict[str, int]] = {}
    for result in results:
        for turn in result["turns"]:
            for item in turn.get("agent_tools", []):
                counts = out.setdefault(str(item["agent"]), {})
                counts[str(item["tool"])] = counts.get(str(item["tool"]), 0) + 1
    return out


def read_calls(project: Path) -> list[dict]:
    """Llamadas medidas del proyecto (``.satellite/observability/calls.jsonl``)."""
    path = project / ".satellite" / "observability" / "calls.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def check(case: dict, turns: list[dict]) -> dict:
    expect = case.get("expect") or {}
    tools = [tool for turn in turns for tool in turn["pilot_tools"] if tool != "ask_user"]
    delegated = [agent for turn in turns for agent in turn["delegations"]]
    out: dict[str, bool] = {}
    if expect.get("no_tools"):
        out["no_tools"] = not tools and not delegated
    if expect.get("tools_any"):
        out["reads_before_answering"] = any(tool in expect["tools_any"] for tool in tools)
    if expect.get("delegate"):
        out[f"delegates_{expect['delegate']}"] = expect["delegate"] in delegated
    if expect.get("delegate_any"):
        out["delegates"] = any(agent in expect["delegate_any"] for agent in delegated)
    if expect.get("asks_first"):
        out["asks_first"] = bool(turns) and turns[0]["asked"]
    if expect.get("selected"):
        out["answer_linked"] = bool(turns) and turns[-1]["selected"] is not None
    if expect.get("uses_graph"):
        out["uses_graph"] = any(turn.get("graph") for turn in turns)
    out["no_failures"] = all(turn["status"] in ("ok", "needs_user_input") for turn in turns)
    return out


def print_table(results: list[dict]) -> None:
    print()
    print(
        f"{'caso':18} {'ok':>5} {'1er texto':>10} {'total':>8} {'llamadas':>9} "
        f"{'entrada':>11} {'salida':>8} {'caché':>11}  fallos"
    )
    sums = [0, 0, 0, 0]
    for result in results:
        checks = result["checks"]
        passed = sum(checks.values())
        first = next((t["first_text_s"] for t in result["turns"] if t["first_text_s"]), None)
        total = sum(t["total_s"] for t in result["turns"])
        row = [sum(t.get(key, 0) for t in result["turns"])
               for key in ("llm_calls", "llm_in", "llm_out", "cache_read")]
        sums = [a + b for a, b in zip(sums, row)]
        failed = ", ".join(name for name, ok in checks.items() if not ok) or "-"
        first_text = f"{first:.1f}s" if first is not None else "-"
        print(
            f"{result['id']:18} {passed:>2}/{len(checks):<2} {first_text:>10} "
            f"{total:>7.1f}s {row[0]:>9} {row[1]:>11} {row[2]:>8} {row[3]:>11}  {failed}"
        )
    print(
        f"{'TOTAL':18} {'':>5} {'':>10} {'':>8} {sums[0]:>9} {sums[1]:>11} "
        f"{sums[2]:>8} {sums[3]:>11}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--cases", type=Path, help="JSON con los casos (lista)")
    parser.add_argument("--only", default="", help="ids separados por coma")
    parser.add_argument("--out", type=Path, help="dónde guardar el informe JSON")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="corridas por caso (una sola varía mucho entre ejecuciones)",
    )
    args = parser.parse_args()

    cases = DEFAULT_CASES
    if args.cases:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
    source = args.project.resolve()
    results = []
    project = None
    repeat = max(1, args.repeat)
    for case in cases:
        for attempt in range(1, repeat + 1):
            label = case["id"] if repeat == 1 else f"{case['id']}#{attempt}"
            # Una copia por corrida: un caso que cambia código no debe alterar el siguiente.
            project = copy_project(source)
            print(f"· {label}… ({project})", flush=True)
            try:
                result = await run_case(project, case)
            except Exception as exc:  # noqa: BLE001 — un caso roto no para la evaluación
                result = {"id": case["id"], "turns": [], "checks": {"runs": False}, "error": str(exc)}
            result["id"] = label
            results.append(result)
    print_table(results)
    summary = tool_summary(results)
    if summary:
        print("\ntools de los agentes delegados:")
        for agent, counts in summary.items():
            ranked = sorted(counts.items(), key=lambda item: -item[1])
            print(f"  {agent:18} " + ", ".join(f"{tool}×{n}" for tool, n in ranked))
    out = args.out or (project.parent if project else Path.cwd()) / "eval_flow_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ninforme: {out}")
    return 0 if all(all(r["checks"].values()) for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
