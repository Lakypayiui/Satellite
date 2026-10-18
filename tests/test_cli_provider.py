"""Comandos ``satellite provider`` (no interactivo + asistente guionizado)."""

import json
from pathlib import Path
import pytest

from typer.testing import CliRunner

from satellite.cli import provider_cli
from satellite.cli.app import app
from satellite.config import providers as pconfig
from satellite.config.models import ModelsResult

# Rich activa color cuando ve CI=true (los runners de GitHub lo ponen) y, con una
# consola de 80 columnas, envuelve el texto de ayuda y parte los nombres de opción
# ("--model" -> "--mo" + "del"), así que las aserciones no los encuentran. Se fija
# un ancho amplio y sin color para que la salida sea la misma aquí y en CI.
runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})


def read(root: Path) -> dict:
    return pconfig.runtime_config(root)


@pytest.fixture(autouse=True)
def isolate_global_providers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SATELLITE_HOME", str(tmp_path / "global"))


def run(*args: str):
    return runner.invoke(app, list(args))


def script(monkeypatch, answers, hidden=()) -> None:
    """Guioniza las preguntas del asistente (Enter => valor por defecto)."""
    pending = iter(answers)
    keys = iter(hidden)

    def fake_prompt(text: str, *, default: str = "") -> str:
        return str(next(pending, "")).strip() or default

    def fake_hidden(text: str) -> str:
        return str(next(keys, ""))

    monkeypatch.setattr(provider_cli, "_prompt", fake_prompt)
    monkeypatch.setattr(provider_cli, "_prompt_hidden", fake_hidden)


def fake_models(monkeypatch, result: ModelsResult) -> None:
    monkeypatch.setattr(provider_cli, "fetch_models", lambda *a, **k: result)


# ---------------------------------------------------------------- no interactivo


def test_list_without_providers(tmp_path: Path) -> None:
    result = run("provider", "--project", str(tmp_path), "list")
    assert result.exit_code == 0
    assert "no hay proveedores guardados" in result.output


def test_add_from_catalog_without_key(tmp_path: Path) -> None:
    result = run(
        "provider", "--project", str(tmp_path),
        "add", "--app", "llamacpp", "--model", "gemma",
    )
    assert result.exit_code == 0, result.output
    data = read(tmp_path)
    assert data["providers"][0]["base_url"] == "http://localhost:8080/v1"
    assert data["providers"][0]["name"] == "openai"
    assert data["llm"]["provider_id"] == data["providers"][0]["id"]
    # activado: el runtime lee las claves top-level
    assert data["model"] == "gemma"


def test_add_with_api_key_env(tmp_path: Path) -> None:
    result = run(
        "provider", "--project", str(tmp_path),
        "add", "--app", "groq", "--model", "llama-3", "--api-key-env", "GROQ_API_KEY",
    )
    assert result.exit_code == 0, result.output
    entry = read(tmp_path)["providers"][0]
    assert entry["api_key_env"] == "GROQ_API_KEY"
    assert entry["base_url"] == "https://api.groq.com/openai/v1"
    assert "api_key" not in entry


def test_add_manual_base_url(tmp_path: Path) -> None:
    result = run(
        "provider", "--project", str(tmp_path),
        "add", "--base-url", "https://mio/v1", "--name", "openai", "--model", "m", "--api-key", "k",
    )
    assert result.exit_code == 0, result.output
    entry = read(tmp_path)["providers"][0]
    assert entry["base_url"] == "https://mio/v1"
    assert entry["api_key"] == "k"


def test_add_requires_url_or_app(tmp_path: Path) -> None:
    result = run("provider", "--project", str(tmp_path), "add", "--model", "m")
    assert result.exit_code != 0
    assert "--app" in result.output or "base-url" in result.output


def test_add_requires_model(tmp_path: Path) -> None:
    result = run("provider", "--project", str(tmp_path), "add", "--app", "groq")
    assert result.exit_code != 0
    assert "--model" in result.output


def test_add_rejects_unknown_family(tmp_path: Path) -> None:
    result = run(
        "provider", "--project", str(tmp_path),
        "add", "--base-url", "https://x/v1", "--name", "inventado", "--model", "m",
    )
    assert result.exit_code != 0
    assert "familia" in result.output


def test_add_no_activate(tmp_path: Path) -> None:
    result = run(
        "provider", "--project", str(tmp_path),
        "add", "--app", "groq", "--model", "m", "--no-activate",
    )
    assert result.exit_code == 0, result.output
    assert read(tmp_path)["llm"]["provider_id"]


def test_catalog_command(tmp_path: Path) -> None:
    result = run("provider", "catalog", "groq")
    assert result.exit_code == 0
    assert "Groq" in result.output
    assert provider_cli.catalog.MANUAL_LABEL in result.output


def test_models_command_ok(monkeypatch) -> None:
    fake_models(monkeypatch, ModelsResult(ok=True, models=("a", "b"), url="https://x/v1/models"))
    result = run("provider", "models", "--base-url", "https://x/v1")
    assert result.exit_code == 0
    assert "a" in result.output and "b" in result.output


def test_models_command_reports_failure(monkeypatch) -> None:
    fake_models(monkeypatch, ModelsResult(ok=False, url="https://x/v1/models", error="OSError: refused"))
    result = run("provider", "models", "--base-url", "https://x/v1")
    assert result.exit_code == 1
    assert "refused" in result.output
    assert "https://x/v1/models" in result.output


def test_use_activates_saved_provider(tmp_path: Path) -> None:
    run("provider", "--project", str(tmp_path), "add", "--app", "groq", "--model", "m", "--no-activate")
    provider_id = read(tmp_path)["providers"][0]["id"]
    result = run("provider", "--project", str(tmp_path), "use", provider_id)
    assert result.exit_code == 0, result.output
    assert read(tmp_path)["llm"]["provider_id"] == provider_id


def test_use_unknown_id_fails(tmp_path: Path) -> None:
    result = run("provider", "--project", str(tmp_path), "use", "nope")
    assert result.exit_code == 1
    assert "no encontrado" in result.output


def test_roles_list_empty_then_set_and_clear(tmp_path: Path) -> None:
    assert "ningún rol configurado" in run("provider", "--project", str(tmp_path), "roles").output
    run("provider", "--project", str(tmp_path), "add", "--app", "groq", "--model", "m")
    provider_id = read(tmp_path)["providers"][0]["id"]

    result = run("provider", "--project", str(tmp_path), "roles", "--role", "creator", "--provider", provider_id)
    assert result.exit_code == 0, result.output
    assert read(tmp_path)["llm"]["creator"]["provider"] == provider_id

    listed = run("provider", "--project", str(tmp_path), "roles")
    assert "creator" in listed.output

    cleared = run("provider", "--project", str(tmp_path), "roles", "--role", "creator", "--clear")
    assert cleared.exit_code == 0
    assert "creator" not in read(tmp_path).get("llm", {})


def test_roles_rejects_unknown_role(tmp_path: Path) -> None:
    result = run("provider", "--project", str(tmp_path), "roles", "--role", "inventado", "--provider", "x")
    assert result.exit_code == 1
    assert "rol desconocido" in result.output


def test_remove_provider(tmp_path: Path) -> None:
    run("provider", "--project", str(tmp_path), "add", "--app", "groq", "--model", "m")
    provider_id = read(tmp_path)["providers"][0]["id"]
    result = run("provider", "--project", str(tmp_path), "remove", provider_id)
    assert result.exit_code == 0
    assert read(tmp_path)["providers"] == []
    assert read(tmp_path)["llm"]["provider_id"] == provider_id  # no se toca el global


def test_remove_unknown_fails(tmp_path: Path) -> None:
    assert run("provider", "--project", str(tmp_path), "remove", "nope").exit_code == 1


def test_doctor_shows_active_provider(tmp_path: Path) -> None:
    run(
        "provider", "--project", str(tmp_path),
        "add", "--app", "groq", "--model", "llama-3", "--api-key-env", "GROQ_API_KEY",
    )
    result = run("doctor", "--project", str(tmp_path))
    assert "proveedor:" in result.output
    assert "llama-3" in result.output
    assert "api_key_env: GROQ_API_KEY" in result.output


# ---------------------------------------------------------------- asistente


def test_wizard_saves_selected_provider(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["groq", "", "1"], hidden=["gsk-123"])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("llama-3", "mixtral"), url="u"))

    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    data = read(tmp_path)
    entry = data["providers"][0]
    assert entry["name"] == "openai"
    assert entry["base_url"] == "https://api.groq.com/openai/v1"
    assert entry["model"] == "llama-3"  # elegido por defecto (el primero)
    assert entry["api_key"] == "gsk-123"
    assert data["model"] == "llama-3"
    assert "guardado y activo" in result.output


def test_wizard_picks_model_by_number(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["groq", "", "2", "1"], hidden=["gsk-123"])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("uno", "dos"), url="u"))
    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert read(tmp_path)["providers"][0]["model"] == "dos"


def test_wizard_loud_when_models_fail(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["groq", "", "mi-modelo", "1"], hidden=["gsk-123"])
    fake_models(monkeypatch, ModelsResult(ok=False, url="https://api.groq.com/openai/v1/models", error="HTTP 401 Unauthorized"))

    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "HTTP 401 Unauthorized" in result.output
    assert "https://api.groq.com/openai/v1/models" in result.output
    assert read(tmp_path)["providers"][0]["model"] == "mi-modelo"


def test_wizard_local_provider_without_key(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["ollama", "", "", "1"], hidden=[])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("llama3",), url="u"))
    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    entry = read(tmp_path)["providers"][0]
    assert entry["base_url"] == "http://localhost:11434/v1"
    assert entry.get("api_key") is None
    assert entry["api_key_env"] == ""
    assert entry["model"] == "llama3"


def test_wizard_manual_openai_compatible(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["manual", "https://mio/v1", "1"], hidden=["mi-clave"])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("modelo-x",), url="u"))
    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    entry = read(tmp_path)["providers"][0]
    assert entry["base_url"] == "https://mio/v1"
    assert entry["api_key"] == "mi-clave"
    assert entry["model"] == "modelo-x"


def test_wizard_can_add_second_provider_at_the_end(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["groq", "", "1", "3", "https://otro/v1", "1", "1"], hidden=["k1", "k2"])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("m1",), url="u"))
    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    data = read(tmp_path)
    assert len(data["providers"]) == 2
    assert data["providers"][1]["base_url"] == "https://otro/v1"
    assert data["model"] == "m1"


def test_wizard_assigns_roles_at_the_end(tmp_path: Path, monkeypatch) -> None:
    script(monkeypatch, ["groq", "", "1", "4", "1", "1"], hidden=["k1"])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("m1",), url="u"))
    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    data = read(tmp_path)
    assert data["llm"]["orchestrator"]["provider"] == data["providers"][0]["id"]
    assert "roles actualizados" in result.output


def test_wizard_reports_pinned_roles(tmp_path: Path, monkeypatch) -> None:
    # el asistente avisa si un rol apunta a OTRO proveedor (no lo pisa en silencio)
    run("provider", "--project", str(tmp_path), "add", "--app", "groq", "--model", "g1", "--no-activate")
    first = read(tmp_path)["providers"][0]["id"]
    run("provider", "--project", str(tmp_path), "add", "--app", "openai", "--model", "g2")  # ahora el activo
    run("provider", "--project", str(tmp_path), "roles", "--role", "creator", "--provider", first)

    script(monkeypatch, ["mistral", "", "1", "1"], hidden=["k2"])
    fake_models(monkeypatch, ModelsResult(ok=True, models=("mixtral",), url="u"))
    result = run("provider", "--project", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "roles con otro proveedor" in result.output
    assert read(tmp_path)["llm"]["creator"]["provider"] == first
