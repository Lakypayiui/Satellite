"""Tests de los modelos de propuesta (Fase 1)."""

import pytest

from satellite.core.errors import ContractError, SpecValidationError
from satellite.core.proposal import (
    AgentProposal,
    ApprovalDecision,
    ApprovalStatus,
    CanCallEntry,
    ProposedAgent,
    ProposedSkill,
    SkillProposal,
)


def make_agent() -> ProposedAgent:
    """Propuesta de agente completa y válida."""
    return ProposedAgent(
        id="docs_writer",
        name="Docs Writer",
        description="Escribe documentación markdown",
        capabilities=("write_docs",),
        tools=("read_file", "write_file"),
        validators=("file_exists",),
        model_role="agent",
        max_turns=1,
        timeout_s=120,
        system_prompt="Escribe docs.",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        output_schema={"type": "object"},
        can_call=(CanCallEntry(agent_id="orchestrator", description="para planificar"),),
        example_tasks=("escribir un README", "documentar una API", "redactar una guía"),
        rationale="No existe ninguna capacidad de documentación",
        reuse_checked=True,
    )


def test_proposed_agent_roundtrip() -> None:
    agent = make_agent()
    restored = ProposedAgent.from_dict(agent.to_dict())
    assert restored == agent
    assert restored.capabilities == ("write_docs",)
    assert restored.can_call[0].agent_id == "orchestrator"


def test_proposed_agent_needs_clarification_partial() -> None:
    agent = ProposedAgent(needs_clarification="¿En qué lenguaje lo quieres?")
    assert agent.is_clarification is True
    assert ProposedAgent.from_dict(agent.to_dict()) == agent


def test_proposed_agent_invalid_id_raises() -> None:
    with pytest.raises(SpecValidationError):
        ProposedAgent(id="Docs Writer", name="X", capabilities=("c",), system_prompt="t", rationale="r")


def test_proposed_agent_requires_capability() -> None:
    with pytest.raises(SpecValidationError):
        ProposedAgent(id="ok_id", name="X", capabilities=(), system_prompt="t", rationale="r")


def test_proposed_agent_requires_rationale() -> None:
    with pytest.raises(SpecValidationError):
        ProposedAgent(id="ok_id", name="X", capabilities=("c",), system_prompt="t", rationale="")


def test_proposed_agent_rejects_empty_tool() -> None:
    with pytest.raises(SpecValidationError):
        ProposedAgent(
            id="ok_id",
            name="X",
            capabilities=("c",),
            tools=("",),
            system_prompt="t",
            rationale="r",
        )


def test_proposed_agent_bad_timeout() -> None:
    with pytest.raises(SpecValidationError):
        ProposedAgent(
            id="ok_id", name="X", capabilities=("c",), system_prompt="t", rationale="r", timeout_s=0
        )


def test_proposed_agent_lifecycle_roundtrip() -> None:
    """`lifecycle` viaja también en la propuesta (esquema v4)."""
    proposal = ProposedAgent(
        id="resumidor",
        name="Resumidor",
        capabilities=("summarize",),
        system_prompt="Eres un resumidor.",
        rationale="hace falta",
        lifecycle="ephemeral",
    )
    assert proposal.to_dict()["lifecycle"] == "ephemeral"
    assert ProposedAgent.from_dict(proposal.to_dict()).lifecycle == "ephemeral"
    # sin el campo (propuestas antiguas) → persistent
    data = {k: v for k, v in proposal.to_dict().items() if k != "lifecycle"}
    assert ProposedAgent.from_dict(data).lifecycle == "persistent"

    with pytest.raises(SpecValidationError, match="lifecycle"):
        ProposedAgent(
            id="resumidor",
            name="Resumidor",
            capabilities=("summarize",),
            system_prompt="Eres un resumidor.",
            rationale="hace falta",
            lifecycle="para-siempre",
        )


def test_can_call_entry_from_string_and_dict() -> None:
    agent = ProposedAgent.from_dict(
        {
            "id": "ok_id",
            "name": "X",
            "capabilities": ["c"],
            "system_prompt": "t",
            "user_template": "t {task}",
            "rationale": "r",
            "can_call": ["orchestrator", {"agent_id": "agent_creator", "description": "d"}],
        }
    )
    assert [c.agent_id for c in agent.can_call] == ["orchestrator", "agent_creator"]
    assert agent.can_call[1].description == "d"


def test_proposed_skill_roundtrip() -> None:
    skill = ProposedSkill(
        id="count_words",
        description="Cuenta palabras de un texto",
        tags=("texto", "util"),
        language="python",
        entrypoint="main.py",
        script="print('{}')",
        inputs={"type": "object"},
        outputs={"type": "object"},
        permissions={"network": False},
        examples=("contar palabras de un párrafo",),
        rationale="Se repite en cada tarea de análisis",
        reuse_checked=True,
    )
    assert ProposedSkill.from_dict(skill.to_dict()) == skill


def test_proposed_skill_allows_empty_script_for_suggestion() -> None:
    # una skill_suggestion puede llegar sin script; el registro lo exige después
    skill = ProposedSkill(id="ok_id", description="d", entrypoint="main.py", rationale="r")
    assert skill.script == ""
    assert ProposedSkill.from_dict(skill.to_dict()) == skill


def test_approval_decision_roundtrip_with_string_status() -> None:
    decision = ApprovalDecision.from_dict(
        {"status": "approved", "reason": "ok", "feedback": ""}
    )
    assert decision.status is ApprovalStatus.APPROVED
    assert decision.approved is True
    assert ApprovalDecision.from_dict(decision.to_dict()) == decision


def test_approval_decision_rejects_bad_status() -> None:
    with pytest.raises(ValueError):
        ApprovalDecision.from_dict({"status": "maybe"})


def test_agent_proposal_roundtrip() -> None:
    proposal = AgentProposal(
        proposal_id="p1",
        task_id="t1",
        agent=make_agent(),
        status=ApprovalStatus.PENDING,
    )
    assert proposal.created_at
    restored = AgentProposal.from_dict(proposal.to_dict())
    assert restored == proposal
    assert restored.agent.id == "docs_writer"


def test_agent_proposal_with_decision() -> None:
    proposal = AgentProposal(
        proposal_id="p1",
        task_id="t1",
        agent=make_agent(),
        status=ApprovalStatus.APPROVED,
        decision=ApprovalDecision(status=ApprovalStatus.APPROVED, reason="ok"),
    )
    restored = AgentProposal.from_dict(proposal.to_dict())
    assert restored.decision is not None
    assert restored.decision.status is ApprovalStatus.APPROVED


def test_agent_proposal_requires_ids() -> None:
    with pytest.raises(ContractError):
        AgentProposal(proposal_id="", task_id="t1", agent=make_agent())


def test_skill_proposal_roundtrip() -> None:
    skill = ProposedSkill(
        id="count_words",
        description="Cuenta palabras",
        entrypoint="main.py",
        script="x",
        rationale="r",
    )
    proposal = SkillProposal(proposal_id="p2", task_id="t2", skill=skill)
    assert SkillProposal.from_dict(proposal.to_dict()) == proposal
