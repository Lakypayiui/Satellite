"""Punto 3 (fiabilidad) — resolución determinista de rutas con nombres ambiguos.

El orden manda: ruta exacta > nombre exacto > difusa; si el nivel ganador tiene varias
coincidencias, el resultado es ambiguo (no se elige al azar). Se respeta la frontera del
proyecto (``..``, absolutas, symlinks) y las exclusiones de archivos sensibles. Se prueba
tanto el resolutor (:mod:`satellite.context.lookup`) como su uso en menciones y en el
``ContextResolver``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from satellite.context.lookup import (
    TIER_EXACT_NAME,
    TIER_EXACT_PATH,
    TIER_FUZZY,
    entries_for,
    lookup,
)
from satellite.context.mentions import parse
from satellite.context.resolver import ContextResolver
from satellite.core.context_models import ContextRef, ContextRefKind


def run(coro):
    return asyncio.run(coro)


def ref(value: str, kind: ContextRefKind = ContextRefKind.PATH) -> ContextRef:
    return ContextRef(kind=kind, value=value)


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_dir_link(link: Path, target: Path) -> bool:
    """Enlace de directorio: symlink o, en Windows sin privilegios, junction."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(target), str(link))
        return True
    except Exception:  # noqa: BLE001 — sin privilegios no se puede crear el enlace
        return False


# --- prioridad: exacta antes que difusa ---------------------------------------


def test_an_exact_path_wins_over_everything(tmp_path: Path) -> None:
    write(tmp_path / "api.py", "raiz")
    write(tmp_path / "src" / "api.py", "otro")

    result = lookup(tmp_path, "api.py")

    assert result.tier == TIER_EXACT_PATH
    assert result.winner is not None and result.winner.relpath == "api.py"
    assert result.ambiguous is False


def test_an_exact_name_wins_over_a_fuzzy_match(tmp_path: Path) -> None:
    write(tmp_path / "user_service.py")
    write(tmp_path / "src" / "user.py")

    result = lookup(tmp_path, "user.py")

    assert result.tier == TIER_EXACT_NAME
    assert result.winner is not None and result.winner.relpath == "src/user.py"
    assert result.ambiguous is False


def test_a_fuzzy_match_is_used_only_when_there_is_no_exact(tmp_path: Path) -> None:
    write(tmp_path / "api_helper.py")

    result = lookup(tmp_path, "api")

    assert result.tier == TIER_FUZZY
    assert result.winner is not None and result.winner.relpath == "api_helper.py"


def test_a_directory_name_resolves_with_kind(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    write(tmp_path / "pkg" / "mod.py")

    result = lookup(tmp_path, "pkg")

    assert result.winner is not None
    assert result.winner.kind == "dir" and result.winner.relpath == "pkg"


# --- múltiples candidatas ------------------------------------------------------


def test_multiple_exact_names_are_ambiguous_and_listed(tmp_path: Path) -> None:
    write(tmp_path / "a" / "api.py")
    write(tmp_path / "b" / "api.py")

    result = lookup(tmp_path, "api.py")

    assert result.tier == TIER_EXACT_NAME
    assert result.ambiguous is True
    assert result.winner is None
    assert result.candidates == ("a/api.py", "b/api.py")


def test_a_relative_path_that_does_not_exist_is_not_reinterpreted(tmp_path: Path) -> None:
    write(tmp_path / "other" / "api.py")

    result = lookup(tmp_path, "src/api.py")

    assert result.matches == ()  # no se re-apunta a other/api.py


# --- frontera de rutas ---------------------------------------------------------


def test_parent_traversal_does_not_resolve(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(tmp_path / "secreto.txt", "fuera")

    assert lookup(project, "../secreto.txt").matches == ()


def test_an_absolute_external_path_does_not_resolve(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = write(tmp_path / "secreto.txt", "fuera")

    assert lookup(project, str(outside)).matches == ()


def test_a_symlink_that_escapes_is_not_a_candidate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = write(tmp_path / "secreto.txt", "fuera")
    link = project / "enlace.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("la plataforma no permite crear symlinks")

    assert lookup(project, "enlace.txt").matches == ()
    assert all(rel != "enlace.txt" for rel, _kind, _name in entries_for(project))


def test_a_directory_symlink_that_escapes_is_not_listed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "otro"
    outside.mkdir()
    write(outside / "x.py", "fuera")
    link = project / "enlazado"
    if not make_dir_link(link, outside):
        pytest.skip("la plataforma no permite crear symlinks/junctions")

    assert lookup(project, "enlazado").matches == ()
    assert all(rel != "enlazado" for rel, _kind, _name in entries_for(project))


# --- archivos sensibles --------------------------------------------------------


def test_sensitive_files_are_not_candidates(tmp_path: Path) -> None:
    write(tmp_path / "app.py")
    write(tmp_path / ".env", "API_KEY=never")
    write(tmp_path / "credentials.json", '{"token": "never"}')

    assert lookup(tmp_path, ".env").matches == ()
    assert lookup(tmp_path, "credentials.json").matches == ()
    assert lookup(tmp_path, "app.py").winner is not None
    names = {name for _rel, _kind, name in entries_for(tmp_path)}
    assert ".env" not in names and "credentials.json" not in names


# --- integración con menciones -------------------------------------------------


def test_a_bare_filename_resolves_by_exact_name(tmp_path: Path) -> None:
    write(tmp_path / "src" / "api.py")

    menciones = parse("mira @api.py", tmp_path)

    assert menciones.refs == (ContextRef(ContextRefKind.PATH, "src/api.py"),)
    assert menciones.missing == () and menciones.ambiguous == ()


def test_an_exact_path_mention_still_wins(tmp_path: Path) -> None:
    write(tmp_path / "src" / "api.py")
    write(tmp_path / "other" / "api.py")

    menciones = parse("mira @src/api.py", tmp_path)

    assert menciones.refs == (ContextRef(ContextRefKind.PATH, "src/api.py"),)
    assert menciones.ambiguous == ()


def test_an_ambiguous_mention_is_reported_not_read(tmp_path: Path) -> None:
    write(tmp_path / "a" / "api.py")
    write(tmp_path / "b" / "api.py")

    menciones = parse("mira @api.py", tmp_path)

    assert menciones.refs == ()
    assert len(menciones.ambiguous) == 1
    assert menciones.ambiguous[0].value == "api.py"
    assert menciones.ambiguous[0].candidates == ("a/api.py", "b/api.py")
    assert "ambiguo" in menciones.summary()


def test_a_bare_name_is_still_a_symbol(tmp_path: Path) -> None:
    write(tmp_path / "src" / "api.py")

    assert parse("¿dónde se define @login?", tmp_path).refs == (
        ContextRef(ContextRefKind.SYMBOL, "login"),
    )


def test_a_mentioned_sensitive_file_is_never_read(tmp_path: Path) -> None:
    write(tmp_path / "credentials.json", '{"token": "never"}')

    # El parseo no es la barrera (puede listar el nombre); el resolutor sí lo veta, y el
    # resolutor de nombres ni siquiera lo ofrece como candidato.
    parse("mira @credentials.json", tmp_path)
    assert lookup(tmp_path, "credentials.json").matches == ()
    assert run(ContextResolver(tmp_path).resolve_ref(ref("credentials.json"))) is None


# --- integración con el ContextResolver ---------------------------------------


def test_the_resolver_reads_by_exact_name(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(project / "src" / "api.py", "contenido api")

    seccion = run(ContextResolver(project).resolve_ref(ref("api.py")))

    assert seccion is not None and seccion.content == "contenido api"


def test_the_resolver_omits_an_ambiguous_name(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(project / "a" / "api.py", "a")
    write(project / "b" / "api.py", "b")

    assert run(ContextResolver(project).resolve_ref(ref("api.py"))) is None


def test_the_resolver_still_blocks_traversal_and_escapes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(tmp_path / "secreto.txt", "fuera")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref("../secreto.txt"))) is None
    assert run(resolver.resolve_ref(ref(str(tmp_path / "secreto.txt")))) is None


def test_the_resolver_does_not_read_a_sensitive_file_by_name(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(project / "credentials.json", '{"token": "never"}')

    assert run(ContextResolver(project).resolve_ref(ref("credentials.json"))) is None
