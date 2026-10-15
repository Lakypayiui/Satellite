"""Tests del AgentProposalGenerator (Fase 5)."""

import asyncio
import json

from satellite.agents.creator import AgentProposalGenerator
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.validator import AgentProposalValidator
from satellite.providers.fake import FakeLLM
from satellite.tools.fs import ReadFileTool
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


PROPOSAL = {
    "type": "proposed_agent",
    "id": "doc_summarizer",
    "name": "Doc Summarizer",
    "description": "resume documentos largos en puntos clave",
    "capabilities": ["summarize_docs", "extract_key_points"],
    "tools": ["read_file"],
    "validators": [],
    "model_role": "generated",
    "max_turns": 6,
    "timeout_s": 120,
    "system_prompt": "# ROL\nResume documentos.",
    "user_template": "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    "example_tasks": [
        "resumir un informe de 40 páginas",
        "extraer los puntos clave de un README",
        "condensar las notas de una reunión",
    ],
    "rationale": "ningún agente resume texto",
    "reuse_checked": True,
}


def make_generator(script, validator=None):
    prompts = DefaultPromptRegistry()
    prompts.register_pair(
        "agent_creator",
        "N:{task} C:{context} F:{feedback} S:{output_schema} T:{tools}",
        "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    )
    tools = DefaultToolRegistry()
    tools.register(ReadFileTool())
    provider = FakeLLM(script=script)
    generator = AgentProposalGenerator(
        provider, DefaultAgentRegistry(), prompts, tools, validator=validator
    )
    return generator, provider


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def test_proposes_agent() -> None:
    generator, _ = make_generator([{"content": _json(PROPOSAL)}])
    outcome = run(generator.propose("necesito resumir documentos"))
    assert outcome.kind == "proposed_agent"
    assert outcome.proposal is not None
    assert outcome.proposal.id == "doc_summarizer"


def test_reuse_outcome() -> None:
    generator, _ = make_generator([{"content": _json({"type": "reuse", "agent_id": "orchestrator"})}])
    outcome = run(generator.propose("resumir"))
    assert outcome.kind == "reuse"
    assert outcome.reuse_candidate == "orchestrator"


def test_needs_clarification_outcome() -> None:
    generator, _ = make_generator(
        [{"content": _json({"type": "needs_clarification", "question": "¿Qué idioma?"})}]
    )
    outcome = run(generator.propose("resumir"))
    assert outcome.kind == "needs_clarification"
    assert outcome.question == "¿Qué idioma?"


def test_invalid_then_valid_retries() -> None:
    generator, provider = make_generator(
        [{"content": "no json"}, {"content": _json(PROPOSAL)}]
    )
    outcome = run(generator.propose("resumir"))
    assert outcome.kind == "proposed_agent"
    assert len(provider.requests) == 2


def test_validator_rejection_feeds_back() -> None:
    validator = AgentProposalValidator(
        DefaultAgentRegistry(),
        tool_ids={"read_file"},
        allowed_roles=("generated",),
    )
    bad = dict(PROPOSAL)
    bad["tools"] = ["ftp_upload"]
    generator, provider = make_generator(
        [{"content": _json(bad)}, {"content": _json(PROPOSAL)}], validator=validator
    )
    outcome = run(generator.propose("subir por ftp"))
    assert outcome.kind == "proposed_agent"
    assert "ftp_upload" in provider.requests[1].messages[-1].content


def test_exhausted_returns_error() -> None:
    generator, _ = make_generator([{"content": "nada"}, {"content": "nada"}])
    outcome = run(generator.propose("x"))
    assert outcome.kind == "error"
    assert outcome.error
