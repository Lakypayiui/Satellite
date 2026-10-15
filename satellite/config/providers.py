"""Lectura/escritura de la configuración de proveedores en ``config.json``.

Reglas (idénticas a las que ya usaba la consola web, ahora en un único sitio):

- Cada proveedor guardado lleva un ``id`` único. Se reutiliza ese ``id`` **solo**
  si coinciden exactamente ``name+model+base_url`` (dos modelos del mismo
  servicio conviven sin pisarse).
- Si no llega ``api_key`` nueva, se conserva la del proveedor previo con el mismo
  ``name+base_url`` (mismo servicio, otro modelo).
- La ``api_key`` nunca se devuelve: hacia fuera siempre ``api_key_set``.
- Guardar **no** toca los roles. El paso explícito :func:`activate` es el que
  deja el proveedor realmente en uso, y avisa de los roles "fijados".
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

CONFIG_PATH = Path(".satellite") / "config" / "config.json"

# Roles que el runtime sabe resolver (``llm.<rol>``) y el orden de la UI.
ROLES: tuple[str, ...] = ("orchestrator", "context", "agents", "creator")


def config_path(project_root: Path | str) -> Path:
    """Ruta de ``config.json`` del proyecto."""
    return Path(project_root) / CONFIG_PATH


def load_config(project_root: Path | str) -> dict:
    """Contenido de ``config.json`` (``{}`` si falta, está vacío o es inválido)."""
    path = config_path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(project_root: Path | str, data: dict) -> Path:
    """Escribe ``config.json`` de forma atómica."""
    path = config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


@dataclass(frozen=True)
class SavedProvider:
    """Proveedor guardado tal como se muestra (sin ``api_key``)."""

    id: str
    name: str
    model: str
    base_url: str
    api_key_env: str = ""
    api_key_set: bool = False

    def to_dict(self) -> dict:
        """Forma pública (la que consumen el CLI y la TUI)."""
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_set": self.api_key_set,
        }

    def label(self) -> str:
        """Etiqueta corta para listados."""
        return f"{self.name} · {self.model}" if self.model else self.name


@dataclass(frozen=True)
class ActivationReport:
    """Resultado de activar un proveedor (qué roles siguieron y cuáles no)."""

    provider: SavedProvider
    previous_id: str = ""
    followed_roles: tuple[str, ...] = ()
    pinned_roles: tuple[str, ...] = ()


def _entries(data: dict) -> list[dict]:
    providers = data.get("providers")
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict)]


def _api_key_set(entry: dict) -> bool:
    if entry.get("api_key"):
        return True
    env = str(entry.get("api_key_env") or "")
    return bool(env and os.getenv(env, ""))


def _to_saved(entry: dict, index: int) -> SavedProvider:
    return SavedProvider(
        id=str(entry.get("id") or f"p{index}"),
        name=str(entry.get("name", "")),
        model=str(entry.get("model", "")),
        base_url=str(entry.get("base_url", "")),
        api_key_env=str(entry.get("api_key_env", "")),
        api_key_set=_api_key_set(entry),
    )


def list_providers(project_root: Path | str) -> list[SavedProvider]:
    """Proveedores guardados (sin ``api_key``)."""
    return [
        _to_saved(entry, index)
        for index, entry in enumerate(_entries(load_config(project_root)))
    ]


def find_provider(project_root: Path | str, provider_id: str) -> SavedProvider | None:
    """Proveedor por ``id`` (:func:`find_provider_or_raise` si es obligatorio)."""
    wanted = str(provider_id)
    for provider in list_providers(project_root):
        if provider.id == wanted:
            return provider
    return None


def find_provider_or_raise(project_root: Path | str, provider_id: str) -> SavedProvider:
    """Proveedor por ``id``; ``KeyError`` con la lista disponible si no existe."""
    provider = find_provider(project_root, provider_id)
    if provider is None:
        known = ", ".join(p.id for p in list_providers(project_root)) or "(ninguno)"
        raise KeyError(f"proveedor no encontrado: '{provider_id}' (ids: {known})")
    return provider


def active_id(project_root: Path | str) -> str:
    """``id`` del proveedor global activo (``llm.provider_id``)."""
    llm = load_config(project_root).get("llm")
    if not isinstance(llm, dict):
        return ""
    return str(llm.get("provider_id", ""))


def active_public(project_root: Path | str) -> dict:
    """Vista pública del proveedor global (contrato de la consola web)."""
    data = load_config(project_root)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    return {
        "name": llm.get("provider", ""),
        "model": llm.get("model", ""),
        "base_url": llm.get("base_url", ""),
        "api_key_env": llm.get("api_key_env", ""),
        "api_key_set": _api_key_set(llm),
    }


def active_provider(project_root: Path | str) -> SavedProvider | None:
    """Proveedor global resuelto (por ``provider_id``; si no, por ``name+model``)."""
    data = load_config(project_root)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    saved = list_providers(project_root)
    provider_id = str(llm.get("provider_id", ""))
    if provider_id:
        for provider in saved:
            if provider.id == provider_id:
                return provider
    name, model = str(llm.get("provider", "")), str(llm.get("model", ""))
    for provider in saved:
        if provider.name == name and provider.model == model:
            return provider
    if name or model:
        return SavedProvider(
            id=provider_id,
            name=name,
            model=model,
            base_url=str(llm.get("base_url", "")),
            api_key_env=str(llm.get("api_key_env", "")),
            api_key_set=_api_key_set(llm),
        )
    # Sin `llm`: el global efectivo del runtime son las claves top-level.
    top_name, top_model = str(data.get("name", "")), str(data.get("model", ""))
    if top_name or top_model:
        return SavedProvider(
            id="",
            name=top_name,
            model=top_model,
            base_url=str(data.get("base_url", "")),
            api_key_env=str(data.get("api_key_env", "")),
            api_key_set=_api_key_set(data),
        )
    return None


def role_config(project_root: Path | str) -> dict[str, dict]:
    """Configuración por rol presente en ``llm.<rol>``."""
    data = load_config(project_root)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    return {
        role: dict(llm[role])
        for role in ROLES
        if isinstance(llm.get(role), dict) and llm[role]
    }


def active_connection(project_root: Path | str) -> dict:
    """Datos de conexión del proveedor activo **incluida la clave** (uso interno).

    Sirve para consultar al proveedor (p. ej. ``/models``) con las mismas
    credenciales con las que se ejecuta el runtime.
    """
    data = load_config(project_root)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    role = llm.get("orchestrator") if isinstance(llm.get("orchestrator"), dict) else {}
    provider_id = str(role.get("provider") or llm.get("provider_id") or "")
    entries = _entries(data)
    entry = next((p for p in entries if str(p.get("id")) == provider_id), None) if provider_id else None
    if isinstance(entry, dict):
        key = str(entry.get("api_key") or "")
        if not key:
            env = str(entry.get("api_key_env") or "")
            key = os.getenv(env, "") if env else ""
        return {
            "id": provider_id,
            "name": str(entry.get("name", "")),
            "model": str(role.get("model") or entry.get("model", "")),
            "base_url": str(entry.get("base_url", "")),
            "api_key": key,
        }
    return {
        "id": provider_id,
        "name": str(llm.get("provider") or data.get("name", "")),
        "model": str(llm.get("model") or data.get("model", "")),
        "base_url": str(llm.get("base_url") or data.get("base_url", "")),
        "api_key": str(llm.get("api_key") or data.get("api_key") or ""),
    }


def api_key_for_base_url(project_root: Path | str, base_url: str) -> str:
    """Clave guardada para esa ``base_url`` (uso interno del servidor)."""
    for entry in _entries(load_config(project_root)):
        if entry.get("base_url") == base_url:
            return str(entry.get("api_key") or "")
    return ""


def save_provider(
    project_root: Path | str,
    *,
    name: str,
    model: str,
    base_url: str = "",
    api_key: str = "",
    api_key_env: str = "",
) -> SavedProvider:
    """Guarda un proveedor y lo deja como global (sin tocar los roles).

    Mismas reglas que la consola web: reutiliza el ``id`` si el proveedor es
    idéntico, conserva la ``api_key`` previa del mismo servicio y escribe
    ``llm.provider/model/base_url/provider_id``.
    """
    data = load_config(project_root)
    providers = list(_entries(data))
    name, model = str(name), str(model)
    base_url, api_key, api_key_env = str(base_url), str(api_key or ""), str(api_key_env or "")

    exact = next(
        (
            p
            for p in providers
            if p.get("name") == name
            and p.get("model") == model
            and p.get("base_url") == base_url
        ),
        None,
    )
    if exact is not None:
        entry = dict(exact)
        if api_key:
            entry["api_key"] = api_key
        if api_key_env:
            entry["api_key_env"] = api_key_env
        providers = [
            p
            for p in providers
            if p is not exact
            and not (p.get("id") == entry.get("id") and entry.get("id"))
        ]
        providers.append(entry)
    else:
        entry = {
            "id": uuid.uuid4().hex[:10],
            "name": name,
            "model": model,
            "base_url": base_url,
            "api_key_env": api_key_env,
        }
        if api_key:
            entry["api_key"] = api_key
        else:
            # mismo servicio (name+base_url), otro modelo: heredar la clave
            previous = next(
                (
                    p
                    for p in providers
                    if p.get("name") == name and p.get("base_url") == base_url
                ),
                None,
            )
            if isinstance(previous, dict) and previous.get("api_key"):
                entry["api_key"] = previous["api_key"]
            elif isinstance(previous, dict) and previous.get("api_key_env"):
                entry["api_key_env"] = previous["api_key_env"]
        providers.append(entry)

    data["providers"] = providers
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    llm["provider"] = name
    llm["model"] = model
    if base_url:
        llm["base_url"] = base_url
    llm["provider_id"] = entry["id"]
    if api_key:
        llm["api_key"] = api_key
    if api_key_env:
        llm["api_key_env"] = api_key_env
    data["llm"] = llm
    save_config(project_root, data)
    return _to_saved(entry, providers.index(entry))


def update_provider(
    project_root: Path | str,
    provider_id: str,
    *,
    name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
) -> SavedProvider:
    """Actualiza un proveedor guardado (por ``id`` o ``name``); clave vacía = igual."""
    data = load_config(project_root)
    providers = list(_entries(data))
    index = next(
        (
            i
            for i, p in enumerate(providers)
            if p.get("id") == provider_id or p.get("name") == provider_id
        ),
        None,
    )
    if index is None:
        raise KeyError(f"proveedor no encontrado: '{provider_id}'")
    entry = dict(providers[index])
    if name is not None:
        entry["name"] = str(name)
    if model is not None:
        entry["model"] = str(model)
    if base_url is not None:
        entry["base_url"] = str(base_url)
    if api_key:
        entry["api_key"] = str(api_key)
    if api_key_env is not None:
        entry["api_key_env"] = str(api_key_env)
    providers[index] = entry
    data["providers"] = providers

    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    if llm.get("provider_id") == entry.get("id"):
        llm["provider"] = entry.get("name", "")
        llm["model"] = entry.get("model", "")
        if entry.get("base_url"):
            llm["base_url"] = entry["base_url"]
        if api_key:
            llm["api_key"] = str(api_key)
        data["llm"] = llm
    save_config(project_root, data)
    return _to_saved(entry, index)


def delete_provider(project_root: Path | str, provider_id: str) -> bool:
    """Elimina un proveedor guardado (por ``id`` o ``name``); ``False`` si no existía."""
    data = load_config(project_root)
    providers = list(_entries(data))
    remaining = [
        p
        for p in providers
        if p.get("id") != provider_id and p.get("name") != provider_id
    ]
    if len(remaining) == len(providers):
        return False
    data["providers"] = remaining
    save_config(project_root, data)
    return True


def activate(
    project_root: Path | str,
    provider_id: str,
    *,
    previous_id: str | None = None,
    roles: Sequence[str] | None = None,
) -> ActivationReport:
    """Deja el proveedor **realmente** en uso por el runtime.

    Escribe el proveedor global (top-level, que es lo que resuelve
    ``runtime_builder._provider_config_from``, **y** ``llm.*``) y sigue el cambio
    en los roles que apuntaban al global anterior. Los roles que apuntan a otro
    proveedor se respetan y se devuelven en ``pinned_roles`` (no hay cascada
    silenciosa). ``roles`` fuerza además esos roles concretos.

    Pasa ``previous_id`` cuando el global ya se haya reescrito (por ejemplo tras
    :func:`save_provider`), para distinguir "seguía al global" de "fijado".
    """
    data = load_config(project_root)
    provider = find_provider(project_root, provider_id)
    if provider is None:
        raise KeyError(f"proveedor no encontrado: '{provider_id}'")
    entries = list(_entries(data))
    entry = next(
        (p for p in entries if str(p.get("id")) == provider.id), None
    ) or {"id": provider.id, "name": provider.name, "model": provider.model}

    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    previous = str(llm.get("provider_id", "")) if previous_id is None else str(previous_id)

    # Global efectivo para el runtime (top-level) + vista de la consola (llm.*)
    data["name"] = provider.name
    data["model"] = provider.model
    if provider.base_url:
        data["base_url"] = provider.base_url
    if entry.get("api_key"):
        data["api_key"] = entry["api_key"]
    llm["provider"] = provider.name
    llm["model"] = provider.model
    if provider.base_url:
        llm["base_url"] = provider.base_url
    llm["provider_id"] = provider.id
    if entry.get("api_key"):
        llm["api_key"] = entry["api_key"]
    if entry.get("api_key_env"):
        llm["api_key_env"] = entry["api_key_env"]

    followed: list[str] = []
    pinned: list[str] = []
    for role in ROLES:
        section = llm.get(role)
        if not isinstance(section, dict) or not section:
            continue
        points_to = str(section.get("provider", ""))
        if role in (roles or ()) or points_to in {previous, "", provider.id}:
            llm[role] = {**section, "provider": provider.id, "model": provider.model}
            followed.append(role)
        elif points_to != provider.id:
            pinned.append(role)

    data["llm"] = llm
    save_config(project_root, data)
    return ActivationReport(
        provider=provider,
        previous_id=previous,
        followed_roles=tuple(followed),
        pinned_roles=tuple(pinned),
    )


def set_roles(project_root: Path | str, roles: dict[str, dict]) -> None:
    """Configura roles por separado (``provider`` = id guardado); vacío = borrar."""
    data = load_config(project_root)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    for role, section in roles.items():
        if role not in ROLES:
            raise KeyError(f"rol desconocido: '{role}' (roles: {', '.join(ROLES)})")
        if not isinstance(section, dict):
            continue
        override = {
            key: value
            for key, value in section.items()
            if value not in (None, "")
            and key in ("provider", "model", "base_url", "api_key", "api_key_env")
        }
        if override:
            llm[role] = override
        else:
            llm.pop(role, None)
    data["llm"] = llm
    save_config(project_root, data)


def clear_roles(project_root: Path | str, roles: Iterable[str] = ROLES) -> None:
    """Borra la configuración de los roles indicados (vuelven al global)."""
    data = load_config(project_root)
    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    for role in roles:
        llm.pop(role, None)
    data["llm"] = llm
    save_config(project_root, data)
