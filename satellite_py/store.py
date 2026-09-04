"""Persistence for Python-side Satellite project state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .registry import AgentDescriptor, AgentRegistry


class AgentStore:
    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root).resolve()
        self.state_root = self.project_root / ".satellite"

    def ensure_dirs(self) -> None:
        for name in ("config", "registry", "agents", "context", "executions"):
            (self.state_root / name).mkdir(parents=True, exist_ok=True)

    def has_state(self) -> bool:
        return self.state_root.is_dir()

    def save_descriptor(self, descriptor: AgentDescriptor) -> Path:
        self.ensure_dirs()
        path = self.state_root / "agents" / f"agent_{descriptor.id}.json"
        path.write_text(json.dumps(_descriptor_to_dict(descriptor), indent=2), encoding="utf-8")
        return path

    def load_descriptors(self) -> list[AgentDescriptor]:
        agents_dir = self.state_root / "agents"
        if not agents_dir.is_dir():
            return []
        descriptors: list[AgentDescriptor] = []
        for path in sorted(agents_dir.glob("agent_*.json")):
            try:
                descriptors.append(_descriptor_from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return descriptors

    def save_registry(self, registry: AgentRegistry) -> Path:
        self.ensure_dirs()
        path = self.state_root / "registry" / "agents.json"
        path.write_text(
            json.dumps([_descriptor_to_dict(agent) for agent in registry.list_agents()], indent=2),
            encoding="utf-8",
        )
        return path

    def load_registry(self) -> AgentRegistry:
        registry = AgentRegistry()
        registry_path = self.state_root / "registry" / "agents.json"
        if registry_path.is_file():
            try:
                entries = json.loads(registry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entries = []
            for entry in entries if isinstance(entries, list) else []:
                try:
                    registry.register_agent(_descriptor_from_dict(entry))
                except (TypeError, KeyError):
                    continue
        for descriptor in self.load_descriptors():
            if registry.find_agent(descriptor.id) is None:
                registry.register_agent(descriptor)
        return registry

    def initialize(self) -> None:
        self.ensure_dirs()
        config_path = self.state_root / "config" / "config.json"
        if not config_path.exists():
            config_path.write_text(
                json.dumps(
                    {
                        "execution": {"backend": "native_process"},
                        "security": {
                            "allow": {
                                "filesystem.read": True,
                                "filesystem.write": False,
                                "process.execute": False,
                                "compiler.execute": False,
                                "network.request": False,
                            }
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        registry_path = self.state_root / "registry" / "agents.json"
        if not registry_path.exists():
            registry_path.write_text("[]\n", encoding="utf-8")


def _descriptor_to_dict(descriptor: AgentDescriptor) -> dict[str, Any]:
    return {
        "id": descriptor.id,
        "name": descriptor.name,
        "description": descriptor.description,
        "version": descriptor.version,
        "input_schema": descriptor.input_schema,
        "output_schema": descriptor.output_schema,
        "context_requirements": descriptor.context_requirements,
        "capabilities": descriptor.capabilities,
        "library_path": descriptor.library_path,
        "enabled": descriptor.enabled,
    }


def _descriptor_from_dict(value: dict[str, Any]) -> AgentDescriptor:
    return AgentDescriptor(
        id=int(value.get("id", 0)),
        name=value.get("name", ""),
        description=value.get("description", ""),
        version=value.get("version", "0.1.0"),
        input_schema=value.get("input_schema", {}),
        output_schema=value.get("output_schema", {}),
        context_requirements=value.get("context_requirements", []),
        capabilities=value.get("capabilities", []),
        library_path=value.get("library_path", ""),
        enabled=value.get("enabled", True),
    )
