"""Tests del AgentProposalValidator (Fase 5)."""

from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.validator import AgentProposalValidator
from satellite.core.proposal import CanCallEntry, ProposedAgent
from satellite.core.spec import AgentSpec


def make_registry() -> DefaultAgentRegistry:
    reg = DefaultAgentRegistry()
    reg.register_spec(
        AgentSpec(
            id="existing_agent",
            name="Existing",
            description="hace otra cosa distinta",
            capabilities=("other",),
            prompt_ref="p",
        )
    )
    return reg


def make_proposal(**overrides) -> ProposedAgent:
    data = dict(
        id="docs_writer",
        name="Docs Writer",
        description="redacta documentación técnica extensa",
        capabilities=("write_docs", "structure_docs"),
        tools=("read_file",),
        validators=(),
        model_role="generated",
        system_prompt="# ROL\nEscribe documentación.",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        example_tasks=(
            "redactar un README",
            "documentar una API",
            "escribir una guía de instalación",
        ),
        rationale="hace falta esta capacidad",
        reuse_checked=True,
    )
    data.update(overrides)
    return ProposedAgent(**data)


def make_validator(**overrides) -> AgentProposalValidator:
    kwargs = dict(
        tool_ids={"read_file", "write_file"},
        allowed_roles=("generated",),
    )
    kwargs.update(overrides)
    return AgentProposalValidator(make_registry(), **kwargs)


def test_valid_proposal() -> None:
    report = make_validator().validate(make_proposal())
    assert report.ok is True
    assert report.errors == ()


def test_clarification_is_ok() -> None:
    report = make_validator().validate(ProposedAgent(needs_clarification="¿qué formato?"))
    assert report.ok is True


def test_unknown_tool_rejected() -> None:
    report = make_validator().validate(make_proposal(tools=("read_file", "ftp_upload")))
    assert report.ok is False
    assert any("ftp_upload" in e for e in report.errors)


def test_reserved_id_rejected() -> None:
    report = make_validator().validate(make_proposal(id="skill_curator"))
    assert report.ok is False
    assert any("reservado" in e for e in report.errors)


def test_bad_id_rejected() -> None:
    # 'ab' es snake_case para el modelo, pero el validador exige ^[a-z][a-z0-9_]{2,40}$
    report = make_validator().validate(make_proposal(id="ab"))
    assert report.ok is False
    assert any("id debe cumplir" in e for e in report.errors)


def test_model_role_not_allowed() -> None:
    report = make_validator().validate(make_proposal(model_role="orchestrator"))
    assert report.ok is False
    assert any("model_role" in e for e in report.errors)


def test_unknown_validator_rejected() -> None:
    report = make_validator().validate(make_proposal(validators=("magic_check",)))
    assert report.ok is False


def test_bad_placeholder_rejected() -> None:
    report = make_validator().validate(
        make_proposal(user_template="Tarea: {task}\n{unknown_ph}")
    )
    assert report.ok is False
    assert any("placeholders" in e for e in report.errors)


def test_dynamic_placeholder_in_system_rejected() -> None:
    report = make_validator().validate(make_proposal(system_prompt="# ROL\n{context}"))
    assert report.ok is False
    assert any("dinámicos" in e for e in report.errors)


def test_missing_task_placeholder_rejected() -> None:
    report = make_validator().validate(
        make_proposal(user_template="Contexto: {context}")
    )
    assert report.ok is False
    assert any("{task}" in e for e in report.errors)


def test_absolute_path_rejected() -> None:
    report = make_validator().validate(
        make_proposal(user_template="Tarea: {task} en C:\\proj\\x.html")
    )
    assert report.ok is False


def test_can_call_unknown_agent_rejected() -> None:
    report = make_validator().validate(
        make_proposal(can_call=(CanCallEntry(agent_id="ghost"),))
    )
    assert report.ok is False
    assert any("ghost" in e for e in report.errors)


def test_can_call_self_rejected() -> None:
    report = make_validator().validate(
        make_proposal(can_call=(CanCallEntry(agent_id="docs_writer"),))
    )
    assert report.ok is False


def test_can_call_existing_agent_ok() -> None:
    report = make_validator().validate(
        make_proposal(can_call=(CanCallEntry(agent_id="existing_agent", description="d"),))
    )
    assert report.ok is True


def test_dedup_without_reuse_flag_rejected() -> None:
    report = make_validator().validate(
        make_proposal(description="hace otra cosa", reuse_checked=False)
    )
    assert report.ok is False
    assert any("duplicado" in e for e in report.errors)


def test_dedup_suppressed_by_reuse_checked() -> None:
    report = make_validator().validate(
        make_proposal(description="hace otra cosa", reuse_checked=True)
    )
    assert report.ok is True


def test_duplicate_id_rejected() -> None:
    report = make_validator().validate(make_proposal(id="existing_agent"))
    assert report.ok is False
    assert any("ya existe" in e for e in report.errors)
