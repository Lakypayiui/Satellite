"""Tests de DefaultSecurityPolicy."""

from pathlib import Path

from satellite.security.policy import DefaultSecurityPolicy


def test_tool_not_listed_denied() -> None:
    policy = DefaultSecurityPolicy(allowed_tools={"read_file"})
    assert policy.check_tool("write_file", {}) is False
    assert policy.check_tool("read_file", {}) is True


def test_empty_allowed_denies_all() -> None:
    policy = DefaultSecurityPolicy()
    assert policy.check_tool("read_file", {}) is False


def test_allowed_tools_override() -> None:
    policy = DefaultSecurityPolicy(allowed_tools={"write_file"})
    # si se pasa la lista del agente, la tool debe estar en ambas
    assert policy.check_tool("write_file", {}, {"write_file"}) is True
    assert policy.check_tool("write_file", {}, set()) is False


def test_request_context_always_allowed() -> None:
    policy = DefaultSecurityPolicy()
    assert policy.check_tool("request_context", {}) is True


def test_path_inside_project_root(tmp_path: Path) -> None:
    policy = DefaultSecurityPolicy()
    assert policy.check_path(tmp_path / "src" / "a.py", tmp_path) is True
    assert policy.check_path(tmp_path, tmp_path) is True


def test_path_outside_project_root(tmp_path: Path, tmp_path_factory) -> None:
    other = tmp_path_factory.mktemp("afuera")
    policy = DefaultSecurityPolicy()
    assert policy.check_path(other / "a.py", tmp_path) is False


def test_traversal_rejected(tmp_path: Path, tmp_path_factory) -> None:
    other = tmp_path_factory.mktemp("afuera2")
    policy = DefaultSecurityPolicy()
    assert policy.check_path(tmp_path / ".." / other.name / "a.py", tmp_path) is False


def test_allowed_paths_extra(tmp_path: Path, tmp_path_factory) -> None:
    extra_root = tmp_path_factory.mktemp("extra")
    policy = DefaultSecurityPolicy(allowed_paths=(extra_root,))
    assert policy.check_path(extra_root / "x.txt", tmp_path) is True
    assert policy.check_path(tmp_path / "y.txt", tmp_path) is True


def test_command_not_allowed() -> None:
    policy = DefaultSecurityPolicy(allow_terminal=False)
    assert policy.check_command(["python", "-c", "1"]) is False
    policy2 = DefaultSecurityPolicy(allow_terminal=True)
    assert policy2.check_command(["python", "-c", "1"]) is False  # no en allowed_commands


def test_command_allowed() -> None:
    policy = DefaultSecurityPolicy(allow_terminal=True, allowed_commands=("python",))
    assert policy.check_command(["python", "-c", "print(1)"]) is True
    assert policy.check_command(["pip", "install"]) is False
