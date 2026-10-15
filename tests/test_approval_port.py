"""Tests del puerto de aprobación y del estado de propuestas (Fase 4)."""

import asyncio

from satellite.approval.base import (
    ApprovalContext,
    AutoApprovalPort,
    DenyAllApprovalPort,
    PendingApprovalPort,
    proposal_id_of,
)
from satellite.approval.cli import CliApproval
from satellite.approval.state import PendingProposals
from satellite.core.proposal import AgentProposal, ApprovalDecision, ApprovalStatus, ProposedAgent


def run(coro):
    return asyncio.run(coro)


def ctx() -> ApprovalContext:
    return ApprovalContext(kind="agent", proposal_id="p1", summary="crear docs_writer")


def test_auto_and_deny_ports() -> None:
    assert run(AutoApprovalPort().request({}, ctx())).status is ApprovalStatus.APPROVED
    assert run(DenyAllApprovalPort().request({}, ctx())).status is ApprovalStatus.REJECTED


def test_proposal_id_of() -> None:
    assert proposal_id_of({"proposal_id": "abc"}, ApprovalContext()) == "abc"
    assert proposal_id_of({"x": 1}, ApprovalContext(proposal_id="z")) == "z"
    assert proposal_id_of({"x": 1}, ApprovalContext()).startswith("prop")


def test_cli_approval_approve() -> None:
    answers = iter(["s"])
    approval = CliApproval(ask=lambda q: next(answers), echo=lambda m: None)
    decision = run(approval.request({}, ctx()))
    assert decision.status is ApprovalStatus.APPROVED


def test_cli_approval_reject() -> None:
    answers = iter(["no"])
    approval = CliApproval(ask=lambda q: next(answers), echo=lambda m: None)
    assert run(approval.request({}, ctx())).status is ApprovalStatus.REJECTED


def test_cli_approval_needs_changes() -> None:
    answers = iter(["cambios", "amplía alcance"])
    approval = CliApproval(ask=lambda q: next(answers), echo=lambda m: None)
    decision = run(approval.request({}, ctx()))
    assert decision.status is ApprovalStatus.NEEDS_CHANGES
    assert decision.feedback == "amplía alcance"


def test_cli_approval_retries_invalid_answer() -> None:
    answers = iter(["xyz", "s"])
    approval = CliApproval(ask=lambda q: next(answers), echo=lambda m: None)
    assert run(approval.request({}, ctx())).status is ApprovalStatus.APPROVED


def test_pending_port_resolved_by_submit() -> None:
    async def scenario():
        port = PendingApprovalPort()
        task = asyncio.create_task(port.request({}, ctx()))
        await asyncio.sleep(0)
        assert port.is_pending("p1") is True
        assert port.pending()[0]["summary"] == "crear docs_writer"
        consumed = port.submit("p1", ApprovalDecision(status=ApprovalStatus.APPROVED, reason="ok"))
        assert consumed is True
        return await task

    decision = run(scenario())
    assert decision.status is ApprovalStatus.APPROVED


def test_pending_port_pre_submitted() -> None:
    async def scenario():
        port = PendingApprovalPort()
        port.submit("p1", ApprovalDecision(status=ApprovalStatus.REJECTED, reason="previo"))
        return await port.request({}, ctx())

    assert run(scenario()).status is ApprovalStatus.REJECTED


def test_pending_port_timeout() -> None:
    port = PendingApprovalPort(timeout_s=0.01)
    decision = run(port.request({}, ctx()))
    assert decision.status is ApprovalStatus.REJECTED
    assert "timeout" in decision.reason


def test_submit_without_pending_returns_false() -> None:
    port = PendingApprovalPort()
    assert port.submit("nope", ApprovalDecision(status=ApprovalStatus.APPROVED)) is False


def test_pending_proposals_lifecycle() -> None:
    registry = PendingProposals()
    proposal = AgentProposal(
        proposal_id="prop-1",
        task_id="t1",
        agent=ProposedAgent(
            id="docs_writer",
            name="Docs Writer",
            capabilities=("write_docs",),
            description="escribe docs",
            system_prompt="x",
            user_template="{task} {context} {feedback}",
            rationale="r",
        ),
    )
    pid = registry.add(proposal, task_id="t1")
    assert pid == "prop-1"
    assert registry.get("prop-1")["status"] == "pending"
    assert len(registry.for_task("t1")) == 1
    resolved = registry.resolve("prop-1", ApprovalDecision(status=ApprovalStatus.APPROVED))
    assert resolved["status"] == "approved"
    assert registry.for_task("t1") == []


def test_pending_proposals_roundtrip() -> None:
    registry = PendingProposals()
    registry.add({"proposal_id": "x"}, task_id="t")
    restored = PendingProposals.from_dict(registry.to_dict())
    assert restored.get("x")["task_id"] == "t"
