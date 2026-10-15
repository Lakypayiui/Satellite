"""Aprobación humana: puerto único (CLI/Web/MCP) y políticas deterministas."""

from satellite.approval.base import (
    ApprovalContext,
    ApprovalPort,
    AutoApprovalPort,
    DenyAllApprovalPort,
    PendingApprovalPort,
    proposal_id_of,
)
from satellite.approval.cli import CliApproval
from satellite.approval.mcp import McpApproval
from satellite.approval.policy import (
    ApprovalPolicy,
    ProposalCounter,
    activate_unrestricted,
)
from satellite.approval.state import PendingProposals
from satellite.approval.tui import TuiApproval
from satellite.approval.web import WebApproval

__all__ = [
    "ApprovalContext",
    "ApprovalPort",
    "AutoApprovalPort",
    "DenyAllApprovalPort",
    "PendingApprovalPort",
    "proposal_id_of",
    "CliApproval",
    "WebApproval",
    "McpApproval",
    "TuiApproval",
    "ApprovalPolicy",
    "ProposalCounter",
    "activate_unrestricted",
    "PendingProposals",
]
