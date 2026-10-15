"""Modal de configuración de proveedor de la TUI.

Flujo: eliges proveedor del catálogo (ya trae la URL base) -> pegas la API key ->
``Cargar modelos`` -> eliges modelo -> ``Guardar``. La última opción del listado
es *Otro OpenAI-compatible (URL manual)*.

El listado de modelos se hace en un hilo aparte para no congelar la interfaz.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from satellite.config import catalog
from satellite.config.models import ModelsResult

MANUAL = "manual"


class ProviderScreen(ModalScreen[str]):
    """Asistente de proveedor. Devuelve un mensaje de resultado (o ``""``)."""

    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, session) -> None:
        super().__init__()
        self.session = session
        self.entry: catalog.CatalogEntry | None = None
        self.status_message = ""

    # --- composición ---

    def compose(self) -> ComposeResult:
        with Container(id="provider-box"):
            yield Static(
                "Elige un proveedor (ya trae su URL), pega tu API key y carga los "
                "modelos. La última opción es para cualquier OpenAI-compatible.",
                id="provider-help",
            )
            yield Input(placeholder="filtrar: groq, kimi, local…", id="provider-search")
            yield OptionList(id="provider-apps")
            yield Input(placeholder="URL base (https://…/v1)", id="provider-url")
            yield Input(
                placeholder="API key (Enter = no guardarla en disco)",
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
            yield Input(placeholder="modelo (se rellena al elegir)", id="provider-model")
            yield Static("", id="provider-status")

    def on_mount(self) -> None:
        self.title = "proveedor"
        self._fill_apps("")

    # --- utilidades ---

    def _status(self, message: str) -> None:
        self.status_message = message
        self.query_one("#provider-status", Static).update(message)

    def _value(self, widget_id: str) -> str:
        return str(self.query_one(f"#{widget_id}", Input).value).strip()

    def _fill_apps(self, query: str) -> None:
        options = self.query_one("#provider-apps", OptionList)
        options.clear_options()
        for entry in catalog.find(query):
            note = f"  ({entry.note})" if entry.note else ""
            options.add_option(
                Option(f"{entry.label}  ·  {entry.base_url}{note}", id=entry.key)
            )
        options.add_option(Option(catalog.MANUAL_LABEL, id=MANUAL))

    def _family(self) -> str:
        return self.entry.family if self.entry is not None else catalog.OPENAI

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
            self._status(
                "Modo manual: escribe la URL base de tu proveedor OpenAI-compatible."
            )
            return
        entry = catalog.get(key)
        if entry is None:
            self._status(f"no conozco ese proveedor: {key}")
            return
        self.entry = entry
        url.value = entry.base_url
        if entry.needs_edit:
            self._status("Esta URL necesita tus datos: revísala y edítala antes de seguir.")
        elif not entry.needs_key:
            self._status(f"{entry.label}: proveedor local, normalmente sin clave.")
        else:
            self._status(f"{entry.label} listo. Pega tu API key y pulsa Cargar modelos.")

    @on(OptionList.OptionSelected, "#provider-models")
    def _pick_model(self, event: OptionList.OptionSelected) -> None:
        self.query_one("#provider-model", Input).value = str(event.option.id or "")

    @on(Button.Pressed, "#provider-load")
    def _load_models(self) -> None:
        url = self._value("provider-url")
        if not url:
            self._status("Falta la URL base.")
            return
        self._status(f"cargando modelos de {url} …")
        self._fetch_models(url, self._family(), self._value("provider-key"))

    @work(thread=True, exclusive=True)
    def _fetch_models(self, url: str, family: str, api_key: str) -> None:
        result = self.session.load_models(url, family, api_key)
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
        self._status(f"{len(result.models)} modelos en {result.url}. Elige uno.")

    @on(Button.Pressed, "#provider-save")
    def _save(self) -> None:
        url = self._value("provider-url")
        model = self._value("provider-model")
        if not url:
            self._status("Falta la URL base.")
            return
        if not model:
            self._status("Falta el modelo: carga los modelos y elige uno (o escríbelo).")
            return
        try:
            saved, report = self.session.save_provider(
                name=self._family(),
                model=model,
                base_url=url,
                api_key=self._value("provider-key"),
                api_key_env=self._value("provider-env"),
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

    @on(Button.Pressed, "#provider-cancel")
    def action_cancel(self) -> None:
        self.dismiss("")
