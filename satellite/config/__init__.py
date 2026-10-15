"""Configuración de proveedores LLM compartida (CLI y TUI).

Única fuente de verdad de las reglas de guardado:
- `id` único por proveedor; se reutiliza solo si `name+model+base_url` coinciden.
- `api_key` se persiste pero **nunca** se expone: siempre `api_key_set`.
- Los roles se configuran por separado (sin cascada automática).
- El `activate` explícito deja el proveedor realmente en uso por el runtime.

No depende de FastAPI, Typer ni Textual.
"""

from satellite.config import catalog, models, providers

__all__ = ["catalog", "models", "providers"]
