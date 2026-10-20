"""Activación de la plantilla desktop_operator desde el CLI (sin LLM)."""

from types import SimpleNamespace

from typer.testing import CliRunner

from satellite.cli.app import app


def test_cli_reports_desktop_operator_as_existing_base_agent(tmp_path, monkeypatch) -> None:
    """Es un agente base: se sincroniza al catálogo, no se crea un duplicado."""
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("SATELLITE_HOME", str(home))

    result = CliRunner().invoke(
        app,
        [
            "agents",
            "--create",
            "desktop_operator",
            "--yes",
            "--project",
            str(project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "exists: desktop_operator" in result.output
    assert (home / "agents" / "desktop_operator.json").is_file()
    assert (home / "agents" / "desktop_operator.system.txt").is_file()
    assert (home / "agents" / "desktop_operator.user.txt").is_file()


def test_cli_non_template_still_uses_the_creator(tmp_path, monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeExpander:
        async def expand(self, need, *, approval_port=None):
            calls.append((need, type(approval_port).__name__))
            return SimpleNamespace(
                kind="reuse",
                spec=None,
                reason="",
                question="",
                reuse_candidate="x",
            )

        async def create_template(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("no debería usar la ruta de plantilla")

    class FakeOrch:
        expander = FakeExpander()

    monkeypatch.setattr(
        "satellite.cli.app.build_runtime",
        lambda project, **kwargs: (FakeOrch(), None, None, None),
    )
    result = CliRunner().invoke(
        app,
        ["agents", "--create", "necesito algo", "--project", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert calls == [("necesito algo", "CliApproval")]
