import json

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


def test_cli_context_build_indexes_symbols(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\nclass Greeter:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "util.cpp").write_text(
        "int add(int a, int b) { return a + b; }\nclass Calc {};\n",
        encoding="utf-8",
    )
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["context", "build"])
    assert result.exit_code == 0
    index = json.loads((tmp_path / ".satellite" / "context" / "index.json").read_text(encoding="utf-8"))
    by_path = {entry["path"]: entry for entry in index["files"]}
    assert set(by_path["main.py"]["symbols"]) >= {"greet", "Greeter"}
    assert set(by_path["util.cpp"]["symbols"]) >= {"add", "Calc"}
    assert by_path["main.py"]["language"] == "Python"
    assert by_path["util.cpp"]["language"] == "C++"


def test_cli_context_build_indexes_dependencies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.cpp").write_text(
        '#include "utils.h"\n#include <vector>\nint main() { return 0; }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "utils.h").write_text("int helper();\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("import math\nfrom utils import add\n", encoding="utf-8")
    (tmp_path / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["context", "build"])
    assert result.exit_code == 0
    index = json.loads((tmp_path / ".satellite" / "context" / "index.json").read_text(encoding="utf-8"))
    by_path = {entry["path"]: entry for entry in index["files"]}
    # includes internos resueltos a path; <vector> y math externos.
    assert "src/utils.h" in by_path["src/app.cpp"]["dependencies"]
    assert any(dep.startswith("external:") for dep in by_path["src/app.cpp"]["dependencies"])
    assert "utils.py" in by_path["main.py"]["dependencies"]
    assert any(dep.startswith("external:") for dep in by_path["main.py"]["dependencies"])


def test_cli_serve_local_requires_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["serve-local"])
    assert result.exit_code == 1
    assert "no inicializado" in result.stdout


def test_cli_serve_local_reports_missing_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    import satellite_py.cli as cli_module

    def fake_glob(self, pattern):
        return iter([])

    monkeypatch.setattr(cli_module.Path, "glob", fake_glob)
    result = runner.invoke(app, ["serve-local"])
    assert result.exit_code == 1
    assert "llama-server" in result.stdout


def test_cli_delegates_to_cpp_when_available(tmp_path, monkeypatch):
    import satellite_py.cli as cli_module

    calls = []

    class _FakeCompleted:
        returncode = 0
        stdout = "salida del cpp\n"
        stderr = ""

    monkeypatch.setattr(cli_module, "_cpp_available", lambda: True)
    monkeypatch.setattr(
        cli_module,
        "_delegate",
        lambda args: calls.append(args) or cli_module.typer.echo("salida del cpp"),
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert calls == [["init"]]
    result = runner.invoke(app, ["agents"])
    assert calls == [["init"], ["agents"]]
    result = runner.invoke(app, ["agent", "info", "1"])
    assert calls == [["init"], ["agents"], ["agent", "info", "1"]]
    assert "salida del cpp" in result.stdout
