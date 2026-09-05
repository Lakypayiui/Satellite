"""Pydantic models for the Satellite web UI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentNode(BaseModel):
    id: int
    name: str = ""
    description: str = ""
    version: str = ""
    capabilities: list[str] = Field(default_factory=list)
    context_requirements: list[str] = Field(default_factory=list)
    has_library: bool = False
    enabled: bool = True
    complements: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    is_native: bool = False


class AgentEdge(BaseModel):
    source: int
    target: int
    capability: str


class AgentGraph(BaseModel):
    nodes: list[AgentNode] = Field(default_factory=list)
    edges: list[AgentEdge] = Field(default_factory=list)


class StepSpec(BaseModel):
    index: int
    agent_id: int
    description: str = ""
    context_needs: dict[str, Any] | None = None
    order: int = 0


class RunRequest(BaseModel):
    goal: str
    user_input: str | None = None
    resume: str | None = None
    max_rounds: int = 3


class RunStart(BaseModel):
    run_id: str


class RunStatus(BaseModel):
    run_id: str
    status: str
    progress: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class FileInfo(BaseModel):
    path: str
    size: int
    language: str = ""
    symbols: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    mtime: float = 0.0


class IndexInfo(BaseModel):
    root: str
    files: list[FileInfo]
    total_files: int
    total_lines: int


class ProviderInfo(BaseModel):
    provider: str
    model: str


class SystemInfo(BaseModel):
    project_root: str | None
    initializated: bool
    provider: ProviderInfo | None
    agents: int
