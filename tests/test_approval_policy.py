"""Tests del motor de políticas de aprobación (Fase 4)."""

import asyncio

from satellite.approval.base import ApprovalContext, ApprovalPort
from satellite.approval.policy import ApprovalPolicy, ProposalCounter, activate_unrestricted
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.kernel.policy import PolicyMode, Quotas


def run(coro):
    return asyncio.run(coro)


class EchoPort(ApprovalPort):
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.calls = 0

    async def request(self, proposal, ctx):
        self.calls += 1
        return self.decision


def test_ask_requires_port() -> None:
    policy = ApprovalPolicy(PolicyMode.ASK)
    assert policy.pre_decide() is None
    decision = run(policy.decide({}, ApprovalContext(), None))
    assert decision.status is ApprovalStatus.REJECTED
    assert "puerto" in decision.reason


def test_ask_delegates_to_port() -> None:
    port = EchoPort(ApprovalDecision(status=ApprovalStatus.APPROVED, reason="usuario"))
    policy = ApprovalPolicy(PolicyMode.ASK)
    decision = run(policy.decide({}, ApprovalContext(), port))
    assert decision.status is ApprovalStatus.APPROVED
    assert port.calls == 1


def test_auto_approves_when_valid() -> None:
    policy = ApprovalPolicy(PolicyMode.AUTO)
    decision = policy.pre_decide(validation_ok=True)
    assert decision is not None and decision.status is ApprovalStatus.APPROVED


def test_auto_rejects_when_invalid() -> None:
    policy = ApprovalPolicy(PolicyMode.AUTO)
    decision = policy.pre_decide(validation_ok=False)
    assert decision is not None and decision.status is ApprovalStatus.REJECTED


def test_unrestricted_approves() -> None:
    policy = ApprovalPolicy(PolicyMode.UNRESTRICTED)
    decision = policy.pre_decide()
    assert decision is not None and decision.status is ApprovalStatus.APPROVED


def test_reserved_always_rejected() -> None:
    for mode in (PolicyMode.ASK, PolicyMode.AUTO, PolicyMode.UNRESTRICTED):
        decision = ApprovalPolicy(mode).pre_decide(reserved=True)
        assert decision is not None
        assert decision.status is ApprovalStatus.REJECTED
        assert "reservado" in decision.reason


def test_quota_exceeded_rejected() -> None:
    for mode in (PolicyMode.ASK, PolicyMode.AUTO, PolicyMode.UNRESTRICTED):
        decision = ApprovalPolicy(mode).pre_decide(quota_ok=False)
        assert decision is not None and decision.status is ApprovalStatus.REJECTED


def test_activate_unrestricted_requires_confirmation() -> None:
    assert activate_unrestricted(True) is PolicyMode.UNRESTRICTED
    assert activate_unrestricted(False) is PolicyMode.ASK


def test_counter_per_task_quota() -> None:
    counter = ProposalCounter(quotas=Quotas(proposals_per_task=2, proposals_per_hour=100))
    assert counter.within_quotas("t1") is True
    counter.register("t1")
    counter.register("t1")
    assert counter.within_quotas("t1") is False
    assert counter.within_quotas("t2") is True


def test_counter_hour_quota_and_pruning() -> None:
    now = {"t": 1000.0}
    counter = ProposalCounter(
        quotas=Quotas(proposals_per_task=100, proposals_per_hour=2),
        clock=lambda: now["t"],
    )
    counter.register("t1")
    counter.register("t1")
    assert counter.within_quotas("t1") is False
    # pasa más de una hora: se podan las antiguas
    now["t"] += 3601.0
    assert counter.hour_count() == 0
    assert counter.within_quotas("t1") is True


def test_requires_human_never_auto_approves() -> None:
    for mode in (PolicyMode.AUTO, PolicyMode.UNRESTRICTED):
        policy = ApprovalPolicy(mode)
        assert policy.pre_decide(requires_human=True) is None


def test_requires_human_without_port_is_rejected() -> None:
    decision = run(
        ApprovalPolicy(PolicyMode.UNRESTRICTED).decide(
            {}, ApprovalContext(kind="desktop_control"), None, requires_human=True
        )
    )
    assert decision.status is ApprovalStatus.REJECTED
    assert "puerto" in decision.reason


def test_requires_human_asks_the_port() -> None:
    port = EchoPort(ApprovalDecision(status=ApprovalStatus.APPROVED, reason="humano"))
    decision = run(
        ApprovalPolicy(PolicyMode.AUTO).decide(
            {}, ApprovalContext(kind="desktop_control"), port, requires_human=True
        )
    )
    assert decision.status is ApprovalStatus.APPROVED
    assert port.calls == 1
