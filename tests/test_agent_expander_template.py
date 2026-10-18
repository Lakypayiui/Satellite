"""AgentExpander.create_template: plantilla → validación → aprobación → registro."""

import asyncio

import pytest

from satellite.agents.expander import AgentExpander
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.store import AgentCatalogStore
from satellite.agents.templates import desktop_operator_template
from satellite.agents.validator import AgentProposalValidator
from satellite.approval.base import ApprovalPort
from satellite.approval.policy import ApprovalPolicy
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.tool_manifest import TOOL_IDS


def run(coro):
    return asyncio.run(coro)


class RecordingPort(ApprovalPort):
    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.contexts: list = []

    async def request(self, proposal, ctx):
        self.contexts.append(ctx)
        status = ApprovalStatus.APPROVED if self.approve else ApprovalStatus.REJECTED
        return ApprovalDecision(status=status, reason="test")


def _expander(tmp_path, *, tool_ids=None):
    registry = DefaultAgentRegistry()
    prompts = DefaultPromptRegistry()
    store = AgentCatalogStore(tmp_path / "agents")
    validator = AgentProposalValidator(
        registry,
        tool_ids=tool_ids if tool_ids is not None else set(TOOL_IDS),
        allowed_roles=("generated",),
    )
    expander = AgentExpander(
        None,
        registry,
        prompts,
        catalog_store=store,
        policy=ApprovalPolicy(),
        validator=validator,
    )
    return expander, registry, prompts, store


def test_create_template_registers_persists_and_preserves_schema(tmp_path) -> None:
    expander, registry, prompts, store = _expander(tmp_path)
    port = RecordingPort(approve=True)
    outcome = run(
        expander.create_template("desktop_operator", approval_port=port)
    )
    assert outcome.kind == "created"
    spec = registry.get("desktop_operator")
    assert spec.output_schema == desktop_operator_template().output_schema
    assert set(spec.tools) == set(TOOL_IDS)
    assert "NO CONFIABLE" in prompts.get_system("desktop_operator")
    assert (store.root / "desktop_operator.json").is_file()
    assert (store.root / "desktop_operator.system.txt").is_file()
    assert (store.root / "desktop_operator.user.txt").is_file()
    assert port.contexts and port.contexts[0].kind == "agent"


def test_create_template_requires_human_approval(tmp_path) -> None:
    expander, registry, _prompts, store = _expander(tmp_path)
    outcome = run(
        expander.create_template(
            "desktop_operator", approval_port=RecordingPort(approve=False)
        )
    )
    assert outcome.kind == "rejected"
    with pytest.raises(KeyError):
        registry.get("desktop_operator")
    assert not (store.root / "desktop_operator.json").exists()


def test_create_template_without_port_is_rejected(tmp_path) -> None:
    expander, registry, _prompts, _store = _expander(tmp_path)
    outcome = run(expander.create_template("desktop_operator"))
    assert outcome.kind == "rejected"
    with pytest.raises(KeyError):
        registry.get("desktop_operator")


def test_unknown_template_is_an_error(tmp_path) -> None:
    expander, _registry, _prompts, _store = _expander(tmp_path)
    outcome = run(
        expander.create_template("no_existe", approval_port=RecordingPort(True))
    )
    assert outcome.kind == "error"
    assert "plantilla desconocida" in outcome.reason


def test_template_validation_failure_does_not_register(tmp_path) -> None:
    expander, registry, _prompts, store = _expander(tmp_path, tool_ids={"read_file"})
    outcome = run(
        expander.create_template(
            "desktop_operator", approval_port=RecordingPort(approve=True)
        )
    )
    assert outcome.kind == "error"
    with pytest.raises(KeyError):
        registry.get("desktop_operator")
    assert not (store.root / "desktop_operator.json").exists()
