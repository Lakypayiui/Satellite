"""Comandos ``satellite provider``: catálogo, asistente interactivo y roles.

El asistente existe para no teclear URLs: eliges proveedor del catálogo (que ya
trae su ``base_url``), pegas tu API key, se cargan los modelos desde esa URL y
eliges el tuyo. Al final puedes configurar otro **OpenAI-compatible** con URL
manual o asignar roles.

La entrada se hace siempre por :func:`_prompt` / :func:`_prompt_hidden` para que
los tests puedan guionizar el asistente.
"""

from __future__ import annotations

from pathlib import Path

import typer

from satellite.cli.console import say
from satellite.config import catalog
from satellite.config import providers as pconfig
from satellite.config.models import default_model, fetch_models

provider_app = typer.Typer(
    help="Configura el proveedor LLM (catálogo de proveedores, modelos y roles)."
)

PROJECT = typer.Option(
    None, "--project", help="Project root (si se omite, el que recibió el grupo)."
)
MAX_MODELS_SHOWN = 60


def _project(ctx: typer.Context, override: Path | None = None) -> Path:
    """Project root efectivo: ``--project`` del subcomando > del grupo > cwd."""
    if override is not None:
        return Path(override)
    if isinstance(ctx.obj, dict) and ctx.obj.get("project"):
        return Path(ctx.obj["project"])
    return Path.cwd()


# ---------------------------------------------------------------- entrada


def _prompt(text: str, *, default: str = "") -> str:
    """Pregunta simple (monkeypatcheable en tests)."""
    return str(typer.prompt(text, default=default, show_default=bool(default))).strip()


def _prompt_hidden(text: str) -> str:
    """Pregunta sin eco (API key)."""
    return str(typer.prompt(text, default="", hide_input=True, show_default=False)).strip()


def _yes(answer: str, *, default: bool = False) -> bool:
    text = str(answer).strip().lower()
    if not text:
        return default
    return text in {"s", "si", "sí", "y", "yes"}


# ---------------------------------------------------------------- salida


def _print_catalog(entries: list[catalog.CatalogEntry], *, query: str = "") -> None:
    if query:
        say(f"  coincidencias para '{query}': {len(entries)}")
    if not entries:
        say(f"  sin coincidencias para '{query}'")
        return
    group = ""
    for index, entry in enumerate(entries, 1):
        if entry.group != group:
            say(f"\n[{entry.group}]")
            group = entry.group
        note = f"  ({entry.note})" if entry.note else ""
        warn = "  <-- edita la URL" if entry.needs_edit else ""
        say(f"  {index:>2}. {entry.label} — {entry.base_url}{note}{warn}")
    say(f"\n  {len(entries) + 1:>2}. {catalog.MANUAL_LABEL}")


def _print_providers(project: Path) -> list[pconfig.SavedProvider]:
    saved = pconfig.list_providers(project)
    active = pconfig.active_id(project)
    if not saved:
        say("(no hay proveedores guardados; usa 'satellite provider')")
        return saved
    for index, provider in enumerate(saved, 1):
        mark = "*" if provider.id == active else " "
        key = "clave ok" if provider.api_key_set else "SIN clave"
        say(f" {mark} {index:>2}. {provider.label()}")
        say(f"       id={provider.id}  familia={provider.name}  {key}")
        say(f"       {provider.base_url}")
    say("\n * = activo")
    return saved


# ---------------------------------------------------------------- pasos del asistente


def _pick_entry() -> catalog.CatalogEntry | None:
    """Elige del catálogo; ``None`` = configurar una URL manual."""
    entries = list(catalog.CATALOG)
    _print_catalog(entries)
    while True:
        answer = _prompt("\nProveedor (número, texto para buscar, o 'manual')")
        if not answer or answer.lower() in {"manual", "m", "otro", "otra"}:
            return None
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(entries):
                return entries[number - 1]
            say(f"  número fuera de rango (1..{len(entries)})")
            continue
        matches = catalog.find(answer)
        if len(matches) == 1:
            say(f"  -> {matches[0].label}")
            return matches[0]
        _print_catalog(matches, query=answer)
        if not matches:
            entries = list(catalog.CATALOG)


def _ask_key(provider_key: str) -> tuple[str, str]:
    """Devuelve ``(api_key, api_key_env)``; ambos vacíos si no hace falta clave."""
    suggested = f"{provider_key.upper().replace('-', '_')}_API_KEY"
    say("\nLa clave se guarda en config.json; si prefieres no dejarla en disco,")
    say(f"deja esto vacío y usa la variable de entorno (sugerida: {suggested}).")
    key = _prompt_hidden("API key (Enter = sin clave en disco)")
    if key:
        return key, ""
    env = _prompt(f"Variable de entorno con la clave [{suggested}]", default=suggested)
    if env and env.upper() in {"NINGUNA", "NONE", "NO", "-"}:
        return "", ""
    return "", env


def _confirm_base_url(base_url: str) -> str:
    if not base_url:
        return _prompt("URL base (p. ej. https://mi-servidor/v1)")
    return _prompt("URL base", default=base_url)


def _pick_family(name: str) -> str:
    while True:
        answer = _prompt("Familia (openai = compatible OpenAI, anthropic)", default=name or catalog.OPENAI)
        if answer.lower() in catalog.FAMILIES:
            return answer.lower()
        say(f"  familia no válida: usa {', '.join(catalog.FAMILIES)}")


def _pick_model(base_url: str, family: str, api_key: str, provider: str = "") -> str:
    """Lista los modelos del proveedor y deja elegir; fallo ruidoso si no puede."""
    if not base_url:
        return _prompt("Modelo (id exacto)")
    result = fetch_models(base_url, family, api_key or None, provider=provider)
    if not result.ok:
        say(f"\n  ✗ no pude listar modelos en {result.url}")
        say(f"    {result.error}")
        say("    escribe el id del modelo a mano (si la URL es incorrecta, corrígela)")
        return _prompt("Modelo (id exacto)")
    if not result.models:
        say(f"\n  ! {result.url} respondió sin modelos ({result.error})")
        return _prompt("Modelo (id exacto)")

    shown = result.models[:MAX_MODELS_SHOWN]
    say(f"\nModelos disponibles en {result.url}:")
    for index, model in enumerate(shown, 1):
        say(f"  {index:>3}. {model}")
    if len(result.models) > len(shown):
        say(f"  ... y {len(result.models) - len(shown)} más")
    answer = _prompt("Modelo (número o id)", default=default_model(result.models))
    if answer.isdigit() and 1 <= int(answer) <= len(result.models):
        return result.models[int(answer) - 1]
    return answer


def _save(
    project: Path,
    *,
    name: str,
    model: str,
    base_url: str,
    api_key: str,
    api_key_env: str,
) -> pconfig.SavedProvider:
    """Guarda, lo deja **realmente** activo y reporta los roles fijados."""
    previous = pconfig.active_id(project)
    saved = pconfig.save_provider(
        project,
        name=name,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    report = pconfig.activate(project, saved.id, previous_id=previous)
    say(f"\n  ✓ guardado y activo: {saved.label()}   id={saved.id}")
    if saved.base_url:
        say(f"    base_url: {saved.base_url}")
    say(f"    api_key: {'configurada' if saved.api_key_set else 'NO configurada (revisa la clave)'}")
    if report.pinned_roles:
        say(f"    ! roles con otro proveedor (no los he tocado): {', '.join(report.pinned_roles)}")
        say("      cámbialos con 'satellite provider roles --role <rol> --provider <id>'")
    return saved


def _assign_roles(project: Path) -> None:
    """Asigna proveedor guardado a cada rol, por separado (sin cascada)."""
    saved = _print_providers(project)
    if not saved:
        return
    updates: dict[str, dict] = {}
    for role in pconfig.ROLES:
        answer = _prompt(
            f"\nProveedor para '{role}' (número, 'global' para heredar, Enter = dejar igual)",
            default="",
        )
        if not answer:
            continue
        if answer.lower() in {"global", "g", "ninguno", "-"}:
            updates[role] = {}
        elif answer.isdigit() and 1 <= int(answer) <= len(saved):
            chosen = saved[int(answer) - 1]
            updates[role] = {"provider": chosen.id, "model": chosen.model}
        else:
            say("  opción no válida; dejo ese rol igual")
    if updates:
        pconfig.set_roles(project, updates)
        say(f"\n  ✓ roles actualizados: {', '.join(sorted(updates))}")


def _run_one(project: Path, entry: catalog.CatalogEntry | None) -> pconfig.SavedProvider:
    """Un ciclo del asistente: proveedor -> clave -> modelos -> guardar."""
    if entry is None:
        say("\nOtro OpenAI-compatible: necesitas su URL base.")
        base_url = _prompt("URL base (p. ej. https://mi-servidor/v1)")
        key_for_env = "openai"
        family = _pick_family(catalog.OPENAI)
    else:
        say(f"\nProveedor: {entry.label}  ({entry.family_label})")
        if entry.note:
            say(f"  nota: {entry.note}")
        base_url = _confirm_base_url(entry.base_url)
        key_for_env = entry.key
        family = entry.family

    if entry is not None and not entry.needs_key:
        say("  (proveedor local: deja la clave vacía si no la pide)")
        api_key, api_key_env = _prompt("API key (Enter = ninguna)", default=""), ""
    else:
        api_key, api_key_env = _ask_key(key_for_env)

    model = _pick_model(base_url, family, api_key, entry.key if entry is not None else "")
    if not model:
        raise typer.BadParameter("hace falta un modelo")
    return _save(
        project,
        name=family,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )


def run_wizard(project: Path) -> None:
    """Asistente completo de configuración de proveedor."""
    say("=== Configurar proveedor LLM ===")
    say(f"proyecto: {Path(project).resolve()}")
    entry: catalog.CatalogEntry | None | bool = _pick_entry()
    while True:
        _run_one(project, entry if isinstance(entry, catalog.CatalogEntry) else None)
        say("\n¿Y ahora?")
        say("  1. Listo")
        say("  2. Configurar otro del catálogo")
        say("  3. Configurar otro OpenAI-compatible (URL manual)")
        say("  4. Asignar roles a proveedores guardados")
        answer = _prompt("Opción", default="1")
        if answer in {"2"}:
            entry = _pick_entry() or None
        elif answer in {"3"}:
            entry = None
        elif answer in {"4"}:
            _assign_roles(project)
            return
        else:
            say("\nListo. Prueba con: satellite doctor")
            return


# ---------------------------------------------------------------- subcomandos


@provider_app.callback(invoke_without_command=True)
def provider_main(
    ctx: typer.Context,
    project: Path = typer.Option(Path.cwd(), "--project", help="Project root."),
) -> None:
    """Configura el proveedor LLM (sin subcomando abre el asistente)."""
    ctx.obj = {"project": Path(project)}
    if ctx.invoked_subcommand is None:
        run_wizard(Path(project))


@provider_app.command("list")
def list_cmd(ctx: typer.Context, project: Path | None = PROJECT) -> None:
    """Lista los proveedores guardados (la clave nunca se muestra)."""
    _print_providers(_project(ctx, project))


@provider_app.command("catalog")
def catalog_cmd(
    query: str = typer.Argument("", help="Texto a buscar en el catálogo."),
) -> None:
    """Muestra el catálogo de proveedores conocidos con su URL base."""
    _print_catalog(catalog.find(query), query=query)


@provider_app.command("models")
def models_cmd(
    ctx: typer.Context,
    app: str = typer.Option("", "--app", help="Clave del catálogo (p. ej. groq)."),
    base_url: str = typer.Option("", "--base-url", help="URL base del proveedor."),
    provider: str = typer.Option("openai", "--provider", help="openai|anthropic."),
    api_key: str = typer.Option("", "--api-key", help="Clave (o usa el entorno)."),
    project: Path | None = PROJECT,
) -> None:
    """Lista los modelos disponibles en una URL (para depurar la conexión)."""
    root = _project(ctx, project)
    entry = catalog.get(app) if app else None
    if entry is not None and not base_url:
        base_url = entry.base_url
        provider = entry.family
    if not base_url:
        raise typer.BadParameter("hace falta --app o --base-url")
    key = api_key or pconfig.api_key_for_base_url(root, base_url) or None
    result = fetch_models(base_url, provider, key, provider=entry.key if entry is not None else "")
    if not result.ok:
        say(f"✗ {result.url}: {result.error}")
        raise typer.Exit(code=1)
    if not result.models:
        say(f"! {result.url}: {result.error}")
        raise typer.Exit(code=1)
    say(f"{len(result.models)} modelos en {result.url}:")
    for model in result.models:
        say(f"  {model}")


@provider_app.command("add")
def add_cmd(
    ctx: typer.Context,
    app: str = typer.Option("", "--app", help="Clave del catálogo (p. ej. ollama)."),
    base_url: str = typer.Option("", "--base-url", help="URL base (si no usas --app)."),
    model: str = typer.Option("", "--model", help="Id del modelo."),
    name: str = typer.Option("", "--name", help="Familia: openai|anthropic."),
    api_key: str = typer.Option("", "--api-key", help="Clave (mejor --api-key-env)."),
    api_key_env: str = typer.Option("", "--api-key-env", help="Variable de entorno con la clave."),
    activate: bool = typer.Option(True, "--activate/--no-activate", help="Dejarlo activo."),
    project: Path | None = PROJECT,
) -> None:
    """Guarda un proveedor sin preguntar nada (para scripts y CI)."""
    root = _project(ctx, project)
    entry = catalog.get(app) if app else None
    if entry is not None and not base_url:
        base_url = entry.base_url
    if not base_url:
        raise typer.BadParameter("hace falta --app del catálogo o --base-url")
    family = (name or (entry.family if entry else catalog.OPENAI)).lower()
    if family not in catalog.FAMILIES:
        raise typer.BadParameter(f"familia no válida: {family} (usa {', '.join(catalog.FAMILIES)})")
    if not model:
        raise typer.BadParameter("hace falta --model (mira 'satellite provider models')")

    previous = pconfig.active_id(root)
    saved = pconfig.save_provider(
        root,
        name=family,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    say(f"✓ guardado: {saved.label()}  id={saved.id}")
    if activate:
        report = pconfig.activate(root, saved.id, previous_id=previous)
        say(f"  activo ({'clave ok' if saved.api_key_set else 'SIN clave'})")
        for role in report.pinned_roles:
            say(f"  ! rol '{role}' sigue con otro proveedor")


@provider_app.command("use")
def use_cmd(
    ctx: typer.Context,
    provider_id: str = typer.Argument(..., help="Id del proveedor guardado."),
    project: Path | None = PROJECT,
) -> None:
    """Deja activo un proveedor guardado (y lo aplica al runtime)."""
    root = _project(ctx, project)
    try:
        report = pconfig.activate(root, provider_id)
    except KeyError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    provider = report.provider
    say(f"✓ activo: {provider.label()}  id={provider.id}")
    say(f"  api_key: {'configurada' if provider.api_key_set else 'NO configurada'}")
    for role in report.pinned_roles:
        say(f"  ! rol '{role}' sigue con otro proveedor")


@provider_app.command("roles")
def roles_cmd(
    ctx: typer.Context,
    role: str = typer.Option("", "--role", help="Rol: " + "|".join(pconfig.ROLES)),
    provider: str = typer.Option("", "--provider", help="Id del proveedor guardado."),
    model: str = typer.Option("", "--model", help="Modelo para ese rol (opcional)."),
    clear: bool = typer.Option(False, "--clear", help="Borra la config del rol."),
    project: Path | None = PROJECT,
) -> None:
    """Configura el proveedor de un rol, por separado (sin cascada)."""
    root = _project(ctx, project)
    if not role:
        current = pconfig.role_config(root)
        if not current:
            say("(ningún rol configurado: todos heredan el global)")
            return
        for name, section in current.items():
            say(f"  {name}: provider={section.get('provider', '')} model={section.get('model', '')}")
        return
    if role not in pconfig.ROLES:
        say(f"rol desconocido: '{role}' (usa {', '.join(pconfig.ROLES)})")
        raise typer.Exit(code=1)
    if clear or not provider:
        pconfig.set_roles(root, {role: {}})
        say(f"✓ rol '{role}' vuelve a heredar el global")
        return
    try:
        target = pconfig.find_provider_or_raise(root, provider)
    except KeyError as exc:
        say(str(exc))
        raise typer.Exit(code=1) from exc
    pconfig.set_roles(root, {role: {"provider": target.id, "model": model or target.model}})
    say(f"✓ rol '{role}' -> {target.label()} (id={target.id})")


@provider_app.command("remove")
def remove_cmd(
    ctx: typer.Context,
    provider_id: str = typer.Argument(..., help="Id del proveedor guardado."),
    project: Path | None = PROJECT,
) -> None:
    """Elimina un proveedor guardado."""
    root = _project(ctx, project)
    if pconfig.active_id(root) == provider_id:
        say(f"! cuidado: '{provider_id}' es el proveedor activo; quedará sin id activo")
    if not pconfig.delete_provider(root, provider_id):
        say(f"proveedor no encontrado: '{provider_id}'")
        raise typer.Exit(code=1)
    say(f"✓ borrado: {provider_id}")
