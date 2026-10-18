"""Selector de proveedor/modelo de la TUI, estilo *spotlight* (quick pick).

Flujo: escribes en la barra de búsqueda (filtrado difuso en vivo) -> eliges un
proveedor del catálogo (ya trae su ``base_url``) -> ``Cargar modelos`` -> eliges un
modelo (Enter lo guarda y lo deja activo). La última opción del listado es
*Otro OpenAI-compatible (URL manual)*.

Si el proveedor pide API key y no hay ninguna, se pide con un paso mínimo (o se toma
de la variable de entorno convencional si ya tiene valor). El listado de modelos se hace
en un hilo aparte para no congelar la interfaz.
"""

from __future__ import annotations

import os

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from satellite.config import catalog
from satellite.config.models import ModelsResult

MANUAL = "manual"

# Proveedores que casi todo el mundo reconoce: van primero en el selector.
_COMMON: tuple[str, ...] = (
    "openai",
    "anthropic",
    "gemini",
    "xai",
    "deepseek",
    "groq",
    "mistral",
    "together",
    "fireworks",
    "openrouter",
    "ollama",
    "lmstudio",
)
_ORDER = {key: index for index, key in enumerate(_COMMON)}

# Variables de entorno convencionales por proveedor (para no pedir la clave si ya está).
_ENV_BY_KEY = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "cohere": "COHERE_API_KEY",
}


def _haystack(entry: catalog.CatalogEntry) -> str:
    """Todo lo por lo que se puede buscar una entrada del catálogo."""
    return f"{entry.key} {entry.label} {entry.group} {entry.base_url} {entry.note}"


def _fuzzy(needle: str, haystack: str) -> bool:
    """Coincidencia difusa: subcadena, o subsecuencia en orden (típico quick pick)."""
    needle = needle.lower()
    haystack = haystack.lower()
    if needle in haystack:
        return True
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


class ApiKeyScreen(ModalScreen[str]):
    """Paso mínimo para pedir la API key cuando falta.

    Devuelve la clave escrita, o ``""`` si se cancela.
    """

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, label: str, *, hint: str = "") -> None:
        super().__init__()
        self.label = label
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Container(id="apikey-box"):
            yield Static(f"API key para {self.label}", id="apikey-title")
            yield Input(placeholder="pega la clave y pulsa Enter", password=True, id="apikey-input")
            if self.hint:
                yield Static(f"o deja vacío para usar {self.hint} del entorno", id="apikey-hint")
            with Horizontal(id="apikey-actions"):
                yield Button("Usar esta clave", variant="success", id="apikey-save")
                yield Button("Cancelar", id="apikey-cancel")

    def on_mount(self) -> None:
        self.query_one("#apikey-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    @on(Button.Pressed, "#apikey-save")
    def _confirm(self) -> None:
        self.dismiss(self.query_one("#apikey-input", Input).value.strip())

    @on(Button.Pressed, "#apikey-cancel")
    def action_cancel(self) -> None:
        self.dismiss("")


class ProviderScreen(ModalScreen[str]):
    """Selector rápido. Devuelve un mensaje de resultado (o ``""``)."""

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.entry: catalog.CatalogEntry | None = None
        self.status_message = ""

    # --- composición ---

    def compose(self) -> ComposeResult:
        with Container(id="provider-box"):
            yield Static("Selecciona proveedor", id="provider-title")
            yield Input(placeholder="Buscar proveedor… (groq, kimi, local)", id="provider-search")
            yield OptionList(id="provider-apps")
            yield Input(placeholder="URL base (https://…/v1)", id="provider-url")
            yield Input(
                placeholder="API key (Enter = usar variable de entorno)",
                password=True,
                id="provider-key",
            )
            yield Input(
                placeholder="variable de entorno con la clave (opcional)",
                id="provider-env",
            )
            with Horizontal(id="provider-actions"):
                yield Button("Cargar modelos", id="provider-load")
                yield Button("Guardar", variant="success", id="provider-save")
                yield Button("Cancelar", id="provider-cancel")
            yield OptionList(id="provider-models")
            yield Input(placeholder="modelo (Enter sobre un resultado lo activa)", id="provider-model")
            yield Static("", id="provider-status")

    def on_mount(self) -> None:
        self.title = "modelo"
        self.query_one("#provider-models", OptionList).display = False
        self._fill_apps("")
        self.query_one("#provider-search", Input).focus()

    # --- utilidades ---

    def _status(self, message: str) -> None:
        self.status_message = message
        self.query_one("#provider-status", Static).update(message)

    def _value(self, widget_id: str) -> str:
        return str(self.query_one(f"#{widget_id}", Input).value).strip()

    def _fill_apps(self, query: str) -> None:
        options = self.query_one("#provider-apps", OptionList)
        options.clear_options()
        needle = str(query or "").strip()
        entries = sorted(catalog.find(""), key=lambda entry: _ORDER.get(entry.key, len(_ORDER)))
        for entry in entries:
            if needle and not _fuzzy(needle, _haystack(entry)):
                continue
            note = f"  ({entry.note})" if entry.note else ""
            options.add_option(
                Option(f"{entry.group} · {entry.label}  —  {entry.base_url}{note}", id=entry.key)
            )
        options.add_option(Option(catalog.MANUAL_LABEL, id=MANUAL))

    def _family(self) -> str:
        return self.entry.family if self.entry is not None else catalog.OPENAI

    def _needs_key(self) -> bool:
        return self.entry is not None and self.entry.needs_key

    def _detected_env(self) -> str:
        """Variable de entorno convencional del proveedor, si ya tiene valor."""
        if self.entry is None:
            return ""
        candidate = _ENV_BY_KEY.get(self.entry.key, f"{self.entry.key.upper()}_API_KEY")
        return candidate if os.environ.get(candidate, "").strip() else ""

    def _credentials(self) -> tuple[str, str]:
        """``(api_key, api_key_env)``: usa la variable de entorno si falta la clave."""
        key = self._value("provider-key")
        env = self._value("provider-env")
        if not key and not env:
            env = self._detected_env()
        return key, env

    # --- eventos ---

    @on(Input.Changed, "#provider-search")
    def _search(self, event: Input.Changed) -> None:
        self._fill_apps(event.value)

    @on(OptionList.OptionSelected, "#provider-apps")
    def _pick_app(self, event: OptionList.OptionSelected) -> None:
        key = str(event.option.id or "")
        url = self.query_one("#provider-url", Input)
        if key == MANUAL:
            self.entry = None
            url.value = ""
            self._status("Modo manual: escribe la URL base de tu proveedor OpenAI-compatible.")
            url.focus()
            return
        entry = catalog.get(key)
        if entry is None:
            self._status(f"no conozco ese proveedor: {key}")
            return
        self.entry = entry
        url.value = entry.base_url
        if entry.needs_edit:
            self._status("Esta URL necesita tus datos: revísala y edítala antes de seguir.")
            url.focus()
        elif not entry.needs_key:
            self._status(f"{entry.label}: proveedor local, normalmente sin clave.")
            self.query_one("#provider-load", Button).focus()
        else:
            detected = self._detected_env()
            aviso = f" (usaré {detected} del entorno)" if detected else ""
            self._status(f"{entry.label} listo{aviso}. Pega tu API key y pulsa Cargar modelos.")
            self.query_one("#provider-key", Input).focus()

    @on(OptionList.OptionSelected, "#provider-models")
    def _pick_model(self, event: OptionList.OptionSelected) -> None:
        """Enter sobre un modelo: lo guarda y lo deja activo de inmediato."""
        self.query_one("#provider-model", Input).value = str(event.option.id or "")
        self._save()

    @on(Button.Pressed, "#provider-load")
    def _load_models(self) -> None:
        url = self._value("provider-url")
        if not url:
            self._status("Falta la URL base.")
            return
        self._status(f"cargando modelos de {url} …")
        provider = self.entry.key if self.entry is not None else ""
        self._fetch_models(url, self._family(), self._value("provider-key"), provider)

    @work(thread=True, exclusive=True)
    def _fetch_models(self, url: str, family: str, api_key: str, provider: str = "") -> None:
        result = self.session.load_models(url, family, api_key, provider)
        self.app.call_from_thread(self._show_models, result)

    def _show_models(self, result: ModelsResult) -> None:
        options = self.query_one("#provider-models", OptionList)
        options.clear_options()
        if not result.ok:
            options.display = False
            self._status(
                f"No pude listar modelos en {result.url}: {result.error}. "
                "Escribe el modelo a mano (revisa la URL si es incorrecta)."
            )
            return
        if not result.models:
            options.display = False
            self._status(
                f"{result.url} respondió sin modelos ({result.error}). "
                "Escribe el modelo a mano."
            )
            return
        for model in result.models:
            options.add_option(Option(model, id=model))
        options.display = True
        self._status(f"{len(result.models)} modelos en {result.url}. Elige uno (Enter lo activa).")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter en los campos del asistente guarda y deja activo el proveedor.

        La barra de búsqueda se deja fuera: ahí Enter no confirma (primero hay que
        elegir un proveedor del listado).
        """
        if event.input.id == "provider-search":
            return
        # Evita que el Enter siga hacia la app y se interprete como un mensaje de chat
        # cuando el modal ya se está cerrando.
        event.stop()
        self._save()

    @on(Button.Pressed, "#provider-save")
    def _save(self, *_args) -> None:
        url = self._value("provider-url")
        model = self._value("provider-model")
        if not url:
            self._status("Falta la URL base.")
            return
        if not model:
            self._status("Falta el modelo: carga los modelos y elige uno (o escríbelo).")
            return
        api_key, api_key_env = self._credentials()
        if self._needs_key() and not api_key and not api_key_env:
            # Paso mínimo: pedir sólo la clave antes de guardar.
            self._ask_key(self._save)
            return
        try:
            saved, report = self.session.save_provider(
                name=self._family(),
                model=model,
                base_url=url,
                api_key=api_key,
                api_key_env=api_key_env,
            )
        except Exception as exc:  # noqa: BLE001 - se muestra al usuario
            self._status(f"No se pudo guardar: {exc}")
            return
        message = f"provider: {saved.label()} (id={saved.id}) activo"
        if not saved.api_key_set:
            message += " — sin clave configurada"
        if report.pinned_roles:
            message += f" — roles con otro proveedor: {', '.join(report.pinned_roles)}"
        self.dismiss(message)

    def _ask_key(self, resume) -> None:
        """Abre el paso mínimo para la API key y reanuda lo que se iba a hacer."""
        etiqueta = self.entry.label if self.entry is not None else "el proveedor"
        self.app.push_screen(
            ApiKeyScreen(etiqueta, hint=self._detected_env()),
            lambda value: self._on_key_entered(value, resume),
        )

    def _on_key_entered(self, value: str | None, resume) -> None:
        if not value:
            self._status("Sin API key: el guardado se canceló.")
            return
        self.query_one("#provider-key", Input).value = value
        resume()

    @on(Button.Pressed, "#provider-cancel")
    def action_cancel(self) -> None:
        self.dismiss("")
