"""Cross-platform command adaptation (Windows .cmd/.bat)."""

from satellite.sandbox import process


def test_cmd_is_wrapped_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(process, "IS_WINDOWS", True)
    argv = process.platform_argv(["npm.cmd", "test", "--", "--run"])
    assert argv[0].lower().endswith("cmd.exe")
    assert argv[1:4] == ["/d", "/s", "/c"]
    assert "npm.cmd" in argv[-1]


def test_bat_is_wrapped_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(process, "IS_WINDOWS", True)
    argv = process.platform_argv(["gradlew.bat", "test"])
    assert argv[0].lower().endswith("cmd.exe")


def test_regular_command_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(process, "IS_WINDOWS", False)
    assert process.platform_argv(("python", "x.py")) == ["python", "x.py"]
