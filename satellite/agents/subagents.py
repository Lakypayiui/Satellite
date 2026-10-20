"""Parallel subagents: ownership guard, budget and isolated child execution.

The parent is the only agent allowed to call ``spawn_agent``. A child receives a
fresh history, the child system prompt and one user message with its task and the
minimum context the parent selected. Children cannot create more agents, cannot
run commands and may only write the files/regions they own.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from satellite.agents.base import AgentContext
from satellite.core.context_models import ContextBundle
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.spec import AgentSpec

MAX_CHILDREN_PER_PARENT = 4
CHILD_SUMMARY_LINES = 5
CHILD_SUMMARY_CHARS = 1200

CHILD_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "failed"]},
        "summary": {"type": "string"},
        "files_modified": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "files_modified"],
    "additionalProperties": False,
}

CHILD_SYSTEM_PROMPT = """# ROLE
You are a subagent. Your only responsibility is the task you received from your parent.

# RULES
1. You may only modify the files or regions listed as owned by you. Every other file is read-only.
2. You cannot create other agents, delegate work or run commands. The parent runs and verifies commands after integration.
3. Respect the contracts and interfaces you were given exactly. Do not change signatures or names, and do not implement work that belongs to another child.
4. Do not inspect or ask for the parent conversation. The task and the selected context are all you receive.
5. If information required for your part is missing, do not invent it: report what is missing and return ``failed``.
6. Keep edits focused on your owned region. Do not rewrite a whole file when the task only needs a local change.

# OUTPUT
Reply with ONLY one valid JSON object, with no markdown and no extra text:
{"status":"completed|failed","summary":"at most 5 lines","files_modified":["relative/or/absolute/path"]}

Use ``completed`` only when your part is finished and internally consistent.
"""

CHILD_USER_TEMPLATE = """# TASK
{task}

# CONTEXT
{context}
"""


@dataclass(frozen=True)
class OwnedPath:
    """One ownership entry: a whole file (``region == ""``) or a named region."""

    path: str
    region: str = ""


@dataclass(frozen=True)
class SpawnResult:
    """Compact result returned to the parent. Never contains code, diffs or logs."""

    agent_id: str
    status: str
    summary: str
    files_modified: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "summary": self.summary,
            "files_modified": list(self.files_modified),
            "error": self.error,
        }


def _base_dir(parent) -> Path:
    raw = getattr(parent, "workdir", None) or getattr(parent, "project_root", None) or "."
    return Path(raw).resolve()


def _normalize(raw: object, base: Path) -> str:
    path = Path(str(raw or "").strip()).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def _inside(path: str, base: Path) -> bool:
    try:
        Path(path).relative_to(Path(os.path.normcase(str(base.resolve()))))
        return True
    except (ValueError, OSError):
        return False


def _parse_owned(raw: object, base: Path, project_root: Path) -> OwnedPath:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("files_owned contains an empty entry")
    file_part, separator, region = text.partition("::")
    path = _normalize(file_part, base)
    if not _inside(path, base) and not _inside(path, project_root):
        raise ValueError(f"owned path is outside the project: {file_part!r}")
    return OwnedPath(path=path, region=region.strip() if separator else "")


def _path_from_arguments(arguments: dict) -> str:
    for key in ("path", "file", "file_path", "out_path", "output", "target"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


@dataclass(frozen=True)
class SpawnGuard:
    """Code-enforced write boundary for one child."""

    owned: tuple[OwnedPath, ...]
    readonly: tuple[str, ...]
    base: Path

    def __post_init__(self) -> None:
        base = Path(self.base).resolve()
        normalized = tuple(
            OwnedPath(
                path=os.path.normcase(str(Path(entry.path).resolve())),
                region=entry.region,
            )
            for entry in self.owned
        )
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "owned", normalized)

    def can_write(self, raw_path: object) -> bool:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return False
        candidate = _normalize(raw_path, self.base)
        return any(candidate == entry.path for entry in self.owned)

    def can_write_arguments(self, arguments: dict) -> bool:
        return self.can_write(_path_from_arguments(arguments))

    def allows_tool(self, tool_name: str, arguments: dict) -> bool:
        """Region ownership forbids whole-file rewrites.

        If a child owns only a region of an existing file, it may use ``edit_file``
        for focused changes, but not ``write_file`` or ``render_diagram``, which
        would replace the entire file and could destroy another child's region.
        """
        path = _path_from_arguments(arguments)
        if not self.can_write(path):
            return False
        if tool_name in {"write_file", "render_diagram"}:
            candidate = _normalize(path, self.base)
            entries = [entry for entry in self.owned if entry.path == candidate]
            if entries and all(entry.region for entry in entries):
                return False
        return True

    @property
    def owned_labels(self) -> str:
        return ", ".join(
            entry.path + (f"::{entry.region}" if entry.region else "")
            for entry in self.owned
        ) or "(none)"


class SpawnManager:
    """Per-parent spawn budget, task-wide ownership registry and child runner."""

    def __init__(self, parent) -> None:
        self.parent = parent

    async def spawn(
        self,
        *,
        task: str,
        context: str,
        files_owned: list[str] | tuple[str, ...],
        files_readonly: list[str] | tuple[str, ...],
    ) -> SpawnResult:
        text = str(task or "").strip()
        if not text:
            return SpawnResult(
                agent_id="",
                status="failed",
                summary="The parent sent an empty task.",
                error="spawn_agent requires a non-empty 'task'.",
            )

        base = _base_dir(self.parent)
        project_root = Path(getattr(self.parent, "project_root", None) or base).resolve()
        try:
            owned = tuple(_parse_owned(item, base, project_root) for item in files_owned)
            readonly = tuple(
                _normalize(item, base)
                for item in files_readonly
                if str(item or "").strip()
            )
        except ValueError as exc:
            return SpawnResult(
                agent_id="",
                status="failed",
                summary=str(exc),
                error=str(exc),
            )

        state = self.parent._task_state.setdefault(
            "subagents",
            {"count": 0, "parents": {}, "ownership": [], "children": []},
        )
        # Each parent gets 1 + 4: the budget is per parent, while ownership is
        # task-global so two parallel parents cannot award the same file/region.
        # The lock lives with the shared state to make reservations atomic.
        lock = state.setdefault("_lock", asyncio.Lock())
        active = getattr(self.parent, "_active_ctx", None)
        parent_key = str(getattr(active, "node_id", "") or f"obj-{id(self.parent)}")
        async with lock:
            parents = state.setdefault("parents", {})
            parent_state = parents.setdefault(parent_key, {"count": 0})
            if parent_state["count"] >= MAX_CHILDREN_PER_PARENT:
                return SpawnResult(
                    agent_id="",
                    status="failed",
                    summary=(
                        f"This parent already created {MAX_CHILDREN_PER_PARENT} "
                        "subagents; the per-parent limit was reached."
                    ),
                    error="subagent_limit_reached",
                )
            existing: list[OwnedPath] = list(state.get("ownership") or [])
            for candidate in owned:
                for previous in existing:
                    if candidate.path != previous.path:
                        continue
                    if not candidate.region or not previous.region or (
                        candidate.region == previous.region
                    ):
                        return SpawnResult(
                            agent_id="",
                            status="failed",
                            summary=(
                                "Ownership conflict: another child already owns "
                                f"{candidate.path}"
                                + (f"::{candidate.region}" if candidate.region else "")
                                + ". Assign disjoint files or regions."
                            ),
                            error="subagent_ownership_conflict",
                        )
            agent_id = f"{self.parent.spec.id}-sub-{uuid.uuid4().hex[:8]}"
            parent_state["count"] += 1
            state["count"] += 1
            state["ownership"].extend(owned)
            state["children"].append(agent_id)

        return await self._run_child(
            agent_id=agent_id,
            task=text,
            context=str(context or "").strip(),
            owned=owned,
            readonly=readonly,
            base=base,
        )

    async def _run_child(
        self,
        *,
        agent_id: str,
        task: str,
        context: str,
        owned: tuple[OwnedPath, ...],
        readonly: tuple[str, ...],
        base: Path,
    ) -> SpawnResult:
        from satellite.agents.runtime import LLMAgent

        parent = self.parent
        guard = SpawnGuard(owned=owned, readonly=readonly, base=base)
        child_spec = replace(
            parent.spec,
            id=agent_id,
            name=f"{parent.spec.name} (subagent)",
            model_role="subagents",
            output_schema=CHILD_OUTPUT_SCHEMA,
            can_call=(),
            max_turns=max(1, int(parent.spec.max_turns)),
            timeout_s=max(1, int(parent.spec.timeout_s)),
        )
        child_tools = [
            tool
            for tool in parent.tools
            if tool.id not in {"spawn_agent", "delegate", "request_context"}
        ]
        child = LLMAgent(
            spec=child_spec,
            system_prompt=CHILD_SYSTEM_PROMPT,
            user_template=CHILD_USER_TEMPLATE,
            tools=child_tools,
            provider=getattr(parent, "spawn_child_provider", None) or parent.provider,
            tool_registry=parent.tool_registry,
            policy=parent.policy,
            context_engine=parent.context_engine,
            project_root=parent.project_root,
            workdir=parent.workdir,
            sandbox=parent.sandbox,
            skills=parent.skills,
            spawn_guard=guard,
            is_child=True,
        )
        child_task = {
            "node_id": agent_id,
            "task": task,
            "context": context,
            "files_owned": [
                entry.path + (f"::{entry.region}" if entry.region else "")
                for entry in owned
            ],
            "files_readonly": list(readonly),
            "parent_agent": parent.spec.id,
        }
        active = getattr(parent, "_active_ctx", None)
        child_ctx = AgentContext(
            context=ContextBundle(),
            feedback=(),
            sink=getattr(active, "sink", None),
            task_id=getattr(active, "task_id", ""),
            node_id=agent_id,
            project_root=parent.project_root,
            user_language="en",
            approval=getattr(active, "approval", None),
            cancel=getattr(active, "cancel", None),
            # Children do not share the parent's tool state or conversation history.
            task_state={},
        )
        try:
            result = await asyncio.wait_for(
                child.execute(child_task, child_ctx),
                timeout=max(1, int(parent.spec.timeout_s)),
            )
        except asyncio.TimeoutError:
            return SpawnResult(
                agent_id=agent_id,
                status="failed",
                summary=(
                    f"Subagent timed out after {max(1, int(parent.spec.timeout_s))}s."
                ),
                error="subagent_timeout",
            )
        except Exception as exc:  # noqa: BLE001 — one child must not break siblings
            return SpawnResult(
                agent_id=agent_id,
                status="failed",
                summary=f"Subagent crashed: {exc}",
                error=str(exc),
            )
        return _spawn_result(agent_id, result)


def _spawn_result(agent_id: str, result: AgentResult) -> SpawnResult:
    output = result.output if isinstance(result.output, dict) else {}
    declared = str(output.get("status") or "").strip().lower()
    if result.status is AgentStatus.OK and declared != "failed":
        status = "completed"
    else:
        status = "failed"
    raw_summary = output.get("summary") or result.summary or result.error or ""
    summary = _clip_summary(raw_summary)
    files = output.get("files_modified")
    if not isinstance(files, list):
        files = _files_from_tools(result)
    files_modified = tuple(
        str(item).strip() for item in files if str(item or "").strip()
    )
    error = ""
    if status == "failed":
        error = str(result.error or output.get("summary") or "subagent_failed")
    return SpawnResult(
        agent_id=agent_id,
        status=status,
        summary=summary,
        files_modified=files_modified,
        error=error,
    )


def _files_from_tools(result: AgentResult) -> list[str]:
    paths: list[str] = []
    for record in result.tool_calls:
        if not record.success or record.tool_name not in {"write_file", "edit_file"}:
            continue
        path = _path_from_arguments(dict(record.arguments or {}))
        if path and path not in paths:
            paths.append(path)
    return paths


def _clip_summary(text: object) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    summary = "\n".join(lines[:CHILD_SUMMARY_LINES])
    if len(summary) > CHILD_SUMMARY_CHARS:
        summary = summary[: CHILD_SUMMARY_CHARS - 1] + "…"
    return summary


def child_spec_for(parent_spec: AgentSpec, agent_id: str) -> AgentSpec:
    """Small helper kept public for tests and future child factories."""
    return replace(
        parent_spec,
        id=agent_id,
        name=f"{parent_spec.name} (subagent)",
        model_role="subagents",
        output_schema=CHILD_OUTPUT_SCHEMA,
        can_call=(),
    )
