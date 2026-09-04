from typer.testing import CliRunner

from satellite_py.cli import app


runner = CliRunner()


def test_cli_init_and_agents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".satellite" / "config" / "config.json").is_file()
    result = runner.invoke(app, ["agents"])
    assert result.exit_code == 0
    assert "ID" in result.stdout


def test_cli_rejects_second_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])
    assert result.exit_code != 0
    assert "already initialized" in result.stdout


def test_cli_context_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["context", "build"])
    assert result.exit_code == 0
    index = tmp_path / ".satellite" / "context" / "index.json"
    assert index.is_file()
    assert "main.py" in index.read_text(encoding="utf-8")
