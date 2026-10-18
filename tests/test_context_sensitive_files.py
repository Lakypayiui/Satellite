"""Punto 3 — exclusión segura por defecto de archivos sensibles (secretos).

Un archivo sensible no entra al índice (``index.json``), no se ofrece como @mención
y no se lee como contexto (prompts). Se respetan las exclusiones previas y se evita
marcar como secreto un archivo que sólo se llama parecido (``secreto.txt``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from satellite.context.project_index import ProjectIndex
from satellite.context.resolver import ContextResolver
from satellite.context.sensitive import is_sensitive_name, is_sensitive_path
from satellite.core.context_models import ContextRef, ContextRefKind


def run(coro):
    return asyncio.run(coro)


def ref(value: str, kind: ContextRefKind = ContextRefKind.PATH) -> ContextRef:
    return ContextRef(kind=kind, value=value)


def test_is_sensitive_name_is_precise() -> None:
    for name in (
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "secrets.yaml",
        "id_rsa",
        "server.key",
        "cert.pem",
        ".npmrc",
    ):
        assert is_sensitive_name(name), name

    for name in ("app.py", "README.md", "secreto.txt", "credenciales.txt", "environment.py"):
        assert not is_sensitive_name(name), name


def test_the_index_excludes_sensitive_files_and_their_content(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=never-store", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"token": "never-store"}', encoding="utf-8")
    (tmp_path / "server.key").write_text("never-store", encoding="utf-8")

    ProjectIndex(tmp_path).ensure_persisted()

    saved = (tmp_path / ".satellite" / "context" / "index.json").read_text(encoding="utf-8")
    assert "app.py" in saved
    for name in (".env", "credentials.json", "server.key"):
        assert name not in saved
    assert "never-store" not in saved


def test_context_for_goal_does_not_leak_secrets(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Proyecto de ejemplo", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=never-store", encoding="utf-8")

    contexto = ProjectIndex(tmp_path).context_for_goal("¿De qué trata el proyecto?")

    assert "never-store" not in contexto
    assert ".env" not in contexto


def test_the_resolver_does_not_read_a_mentioned_secret_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("ok", encoding="utf-8")
    (project / ".env").write_text("API_KEY=never-store", encoding="utf-8")
    (project / "id_rsa").write_text("PRIVADA", encoding="utf-8")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref(".env"))) is None
    assert run(resolver.resolve_ref(ref("id_rsa"))) is None

    interno = run(resolver.resolve_ref(ref("app.py")))
    assert interno is not None and interno.content == "ok"


def test_a_dir_listing_omits_sensitive_names(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("ok", encoding="utf-8")
    (project / ".env").write_text("x", encoding="utf-8")
    resolver = ContextResolver(project)

    listado = run(resolver.resolve_ref(ref(".", ContextRefKind.DIR)))

    assert listado is not None
    assert "app.py" in listado.content
    assert ".env" not in listado.content


def test_a_pattern_does_not_pull_secret_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("ok", encoding="utf-8")
    (project / ".env").write_text("API_KEY=never-store", encoding="utf-8")
    resolver = ContextResolver(project)

    patron = run(resolver.resolve_ref(ref("*", ContextRefKind.PATTERN)))

    assert patron is not None  # app.py sí entra
    assert "never-store" not in patron.content


def test_a_normal_file_named_secreto_is_not_sensitive(tmp_path: Path) -> None:
    """Guard: no se marca por subcadena, así que «secreto.txt» se sigue leyendo."""
    assert not is_sensitive_path(tmp_path / "secreto.txt")

    project = tmp_path / "project"
    project.mkdir()
    (project / "secreto.txt").write_text("contenido normal", encoding="utf-8")

    seccion = run(ContextResolver(project).resolve_ref(ref("secreto.txt")))

    assert seccion is not None and seccion.content == "contenido normal"
