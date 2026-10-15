"""Aprobación humana: puerto único (CLI/TUI/MCP) y políticas deterministas."""

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

__all__ = [
    "ApprovalContext",
    "ApprovalPort",
    "AutoApprovalPort",
    "DenyAllApprovalPort",
    "PendingApprovalPort",
    "proposal_id_of",
    "CliApproval",
    "McpApproval",
    "TuiApproval",
    "ApprovalPolicy",
    "ProposalCounter",
    "activate_unrestricted",
    "PendingProposals",
]
