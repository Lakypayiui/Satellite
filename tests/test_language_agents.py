"""The 20 language coder/auditor base agents."""

from pathlib import Path

from satellite.agents.registry import DefaultAgentRegistry
from satellite.kernel.manifest import (
    RESERVED_AGENT_IDS,
    SPAWNABLE_RESERVED_AGENT_IDS,
)

PKG = Path(__file__).resolve().parent.parent / "satellite" / "agents"
SPECS = PKG / "specs"
PROMPTS = PKG / "prompts"

LANGUAGES = (
    "python",
    "javascript",
    "typescript",
    "java",
    "csharp",
    "cpp",
    "sql",
    "go",
    "php",
    "rust",
)

CODER_TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "search_files",
    "lsp",
    "code_task",
    "run_command",
}
AUDITOR_TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "search_files",
    "git_status",
    "lsp",
    "code_task",
}


def _registry() -> DefaultAgentRegistry:
    registry = DefaultAgentRegistry()
    registry.load_dir(SPECS)
    return registry


def test_language_pairs_exist() -> None:
    registry = _registry()
    for language in LANGUAGES:
        for role in ("coder", "auditor"):
            agent_id = f"{language}_{role}"
            spec = registry.get(agent_id)
            assert spec.prompt_ref == agent_id
            assert spec.output_schema is not None
            assert "summary" in spec.output_schema["required"]
            assert (PROMPTS / f"{agent_id}.system.txt").is_file()
            assert (PROMPTS / f"{agent_id}.user.txt").is_file()


def test_coder_tools_and_capabilities() -> None:
    registry = _registry()
    for language in LANGUAGES:
        coder = registry.get(f"{language}_coder")
        assert set(coder.tools) == CODER_TOOLS
        assert set(coder.capabilities) == {
            f"implement_{language}",
            f"write_{language}",
            f"edit_{language}",
            f"{language}_code",
        }
        assert coder.max_turns == 40
        assert coder.timeout_s == 600


def test_auditor_tools_and_capabilities() -> None:
    registry = _registry()
    for language in LANGUAGES:
        auditor = registry.get(f"{language}_auditor")
        assert set(auditor.tools) == AUDITOR_TOOLS
        assert set(auditor.capabilities) == {
            f"audit_{language}",
            f"review_{language}",
            f"test_{language}",
            f"{language}_tests",
        }
        assert auditor.max_turns == 30
        assert auditor.timeout_s == 600


def test_language_coders_can_spawn_children_but_auditors_cannot() -> None:
    registry = _registry()
    for language in LANGUAGES:
        coder = registry.get(f"{language}_coder")
        auditor = registry.get(f"{language}_auditor")
        assert coder.id in RESERVED_AGENT_IDS
        assert coder.id in SPAWNABLE_RESERVED_AGENT_IDS
        assert auditor.id in RESERVED_AGENT_IDS
        assert auditor.id not in SPAWNABLE_RESERVED_AGENT_IDS
        # The factory adds spawn_agent dynamically; it is not in spec.tools.
        assert "spawn_agent" not in coder.tools
        assert "spawn_agent" not in auditor.tools
        assert "delegate" not in coder.tools
        assert "delegate" not in auditor.tools
