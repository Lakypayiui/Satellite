"""Catálogo único de comandos y acciones, compartido por la TUI, la CLI y la app.

Aquí vive **una sola definición**. Cada superficie la consume en vez de mantener su
propia lista: la paleta de la TUI la importa directo, la CLI la publica con
``satellite commands`` y la app la pide por ``GET /api/commands``.

Paridad (el criterio): toda acción de la TUI o de la CLI aparece **también** en la paleta
del escritorio. ``surfaces`` dice en qué paletas aparece cada comando y ``parity()`` informa
de lo que falte de verdad. Cuando una acción no tiene sentido ejecutarla dentro de una app
gráfica (abrir la TUI), no se esconde tras la superficie: se marca con ``terminal_only`` y
la app la **muestra** con el comando exacto para una terminal, sin fingir que puede abrirla
por ti.

Sin dependencias: sólo stdlib, para que importarlo no arrastre el runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

SURFACES: tuple[str, ...] = ("tui", "cli", "desktop")


@dataclass(frozen=True)
class ArgSpec:
    """Un dato que una acción pide antes de ejecutarse (paleta con campos guiados)."""

    key: str
    label: str
    optional: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class CommandSpec:
    """Un comando/acción del catálogo: qué hace y dónde está disponible."""

    id: str
    category: str
    summary: str
    usage: str = ""
    args: tuple[ArgSpec, ...] = ()
    surfaces: tuple[str, ...] = SURFACES
    needs_runtime: bool = False
    # No se ejecuta dentro de la app: se ofrece el comando para una terminal (ver `tui`).
    terminal_only: bool = False

    def in_surface(self, surface: str) -> bool:
        """Aparece como comando de paleta en esa superficie."""
        return surface in self.surfaces

    def to_dict(self) -> dict:
        """Vista serializable (la que sirve ``GET /api/commands``)."""
        return {
            "id": self.id,
            "category": self.category,
            "summary": self.summary,
            "usage": self.usage,
            "needs_runtime": self.needs_runtime,
            "terminal_only": self.terminal_only,
            "surfaces": list(self.surfaces),
            "args": [
                {
                    "key": arg.key,
                    "label": arg.label,
                    "optional": arg.optional,
                    "placeholder": arg.placeholder,
                }
                for arg in self.args
            ],
        }


# Orden = orden en que se agrupan las paletas (la TUI en /help, la app por categoría).
CATALOG: tuple[CommandSpec, ...] = (
    # ---------------------------------------------------------------- Proyecto
    CommandSpec("project", "Proyecto", "Abrir otra carpeta de proyecto", surfaces=("desktop",)),
    CommandSpec(
        "sessions", "Proyecto", "Ver las conversaciones guardadas",
        usage="/sessions", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "new", "Proyecto", "Empezar una conversación nueva",
        usage="/new", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "session", "Proyecto", "Crea, reanuda, renombra, fija o archiva una conversación",
        usage="/session <new|use|name|pin|unpin|archive|unarchive> [valor]",
        args=(
            ArgSpec("accion", "new | use | name | pin | unpin | archive | unarchive"),
            ArgSpec("valor", "Id o nombre (o el nombre nuevo)", optional=True),
        ),
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "git", "Proyecto", "Estado real de git (rama, cambios, conflictos)",
        usage="/git", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "worktrees", "Proyecto", "Worktrees del repo y contexto de ejecución activo",
        usage="/worktrees [list|add|use|remove]", surfaces=("tui", "cli", "desktop"),
    ),
    # En la TUI/CLI no hay diff visual: el ancla se da explícitamente (archivo + lado + rango).
    CommandSpec(
        "comments", "Proyecto", "Comentarios inline del diff: listar, crear, resolver y borrar",
        usage="/comments [list|add|resolve|reopen|delete]",
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "panel-left", "Proyecto", "Mostrar u ocultar el panel izquierdo",
        surfaces=("desktop",),
    ),
    CommandSpec(
        "panel-right", "Proyecto", "Mostrar u ocultar el inspector",
        surfaces=("desktop",),
    ),

    # ----------------------------------------------------------- Conversación
    CommandSpec(
        "run", "Conversación", "Envía un mensaje al agente (igual que texto libre)",
        usage="/run <mensaje>",
        args=(ArgSpec("mensaje", "Mensaje para el agente"),),
        surfaces=("tui", "cli", "desktop"), needs_runtime=True,
    ),
    CommandSpec(
        "plan", "Conversación", "Modo plan: el LLM propone alternativas para elegir",
        usage="/plan <tarea>",
        args=(ArgSpec("tarea", "Tarea ambigua a planificar"),),
        surfaces=("tui", "cli", "desktop"), needs_runtime=True,
    ),
    CommandSpec(
        "context", "Conversación", "Uso de contexto del modelo",
        usage="/context", surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "compact", "Conversación", "Compactar el contexto de la conversación",
        usage="/compact", surfaces=("tui", "cli", "desktop"), needs_runtime=True,
    ),
    CommandSpec(
        "cancel", "Conversación", "Cancelar el turno en curso",
        usage="/cancel [motivo]", surfaces=("tui", "desktop"), needs_runtime=True,
    ),
    CommandSpec(
        "history", "Conversación", "Muestra la conversación actual",
        usage="/history", surfaces=("tui", "desktop"),
    ),
    # La cola es la "instrucción siguiente" real: se ejecuta al terminar el turno (o con
    # «run»), nunca edita el turno en curso. En el escritorio se ve/edita en el compositor.
    CommandSpec(
        "queue", "Conversación", "Cola de instrucciones: listar, encolar, editar y ejecutar",
        usage="/queue [list|add|edit|remove|clear|run]",
        surfaces=("tui", "cli", "desktop"), needs_runtime=False,
    ),

    # ------------------------------------------------------ Agentes y skills
    CommandSpec(
        "agents", "Agentes y skills", "Ver el catálogo de agentes",
        usage="/agents", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "create", "Agentes y skills", "Crear un agente para una necesidad",
        usage="/create <necesidad>",
        args=(ArgSpec("necesidad", "¿Qué capacidad hace falta?"),),
        surfaces=("tui", "cli", "desktop"), needs_runtime=True,
    ),
    CommandSpec(
        "skills", "Agentes y skills", "Ver el catálogo de skills",
        usage="/skills", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "skill-search", "Agentes y skills", "Buscar en las skills",
        usage="/skill-search <texto>",
        args=(ArgSpec("texto", "Texto a buscar"),),
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "skill-edit", "Agentes y skills", "Editar una skill",
        usage="/skill-edit <id> [--description <t>] [--tags a,b]",
        args=(
            ArgSpec("id", "Id de la skill"),
            ArgSpec("descripcion", "Nueva descripción", optional=True),
            ArgSpec("tags", "Tags separados por coma", optional=True),
        ),
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "skill-delete", "Agentes y skills", "Eliminar una skill",
        usage="/skill-delete <id>",
        args=(ArgSpec("id", "Id de la skill"),),
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "curate", "Agentes y skills", "Ejecutar el curador de skills",
        usage="/curate [petición]",
        args=(ArgSpec("peticion", "Petición (opcional)", optional=True),),
        surfaces=("tui", "desktop"), needs_runtime=True,
    ),
    CommandSpec(
        "tools", "Agentes y skills", "Ver las herramientas del runtime",
        usage="satellite tools list", surfaces=("cli", "desktop"),
    ),

    # ------------------------------------------------------ Tareas y trabajos
    CommandSpec(
        "tasks", "Tareas y trabajos", "Listar las tareas persistidas",
        usage="/tasks", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "status", "Tareas y trabajos", "Eventos de una tarea",
        usage="/status <task_id>",
        args=(ArgSpec("task_id", "Id de la tarea"),),
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "inspect", "Tareas y trabajos", "Grafo serializado de una tarea",
        usage="/inspect <task_id>",
        args=(ArgSpec("task_id", "Id de la tarea"),),
        surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "changes", "Tareas y trabajos", "Archivos y diff que cambió una tarea",
        usage="/changes [task_id]", surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "undo-task", "Tareas y trabajos", "Restaurar los cambios de esa tarea",
        usage="/undo-task [task_id]",
        args=(ArgSpec("task_id", "Id de la tarea"),),
        surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "jobs", "Tareas y trabajos", "Comandos largos en segundo plano",
        usage="/jobs [list|output|wait|stop]", surfaces=("tui", "desktop"),
    ),
    # La TUI cubre salida/espera/parada con subcomandos de /jobs; la app las ofrece sueltas.
    CommandSpec(
        "job-output", "Tareas y trabajos", "Ver la salida de un trabajo",
        usage="/jobs output <id>",
        args=(ArgSpec("id", "Id del trabajo"),),
        surfaces=("desktop",),
    ),
    CommandSpec(
        "job-wait", "Tareas y trabajos", "Esperar a que termine un trabajo",
        usage="/jobs wait <id> [s]",
        args=(
            ArgSpec("id", "Id del trabajo"),
            ArgSpec("segundos", "Segundos de espera", optional=True),
        ),
        surfaces=("desktop",),
    ),
    CommandSpec(
        "job-stop", "Tareas y trabajos", "Detener un trabajo",
        usage="/jobs stop <id>",
        args=(ArgSpec("id", "Id del trabajo"),),
        surfaces=("desktop",),
    ),

    # --------------------------------------------------- Proveedores y roles
    CommandSpec(
        "provider", "Proveedores y roles", "Configurar el proveedor (catálogo + modelos)",
        usage="/provider [list]", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "providers", "Proveedores y roles", "Ver los proveedores guardados",
        surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "provider-catalog", "Proveedores y roles", "Ver el catálogo de proveedores conocidos",
        usage="satellite provider catalog", surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "provider-models", "Proveedores y roles", "Listar los modelos de una URL",
        usage="satellite provider models",
        args=(
            ArgSpec("base_url", "URL base (vacío = proveedor activo)", optional=True),
            ArgSpec("family", "openai | anthropic", optional=True),
            ArgSpec("api_key", "Clave (opcional)", optional=True),
        ),
        surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "provider-add", "Proveedores y roles", "Guardar un proveedor sin asistente",
        usage="satellite provider add",
        args=(
            ArgSpec("name", "Familia: openai | anthropic"),
            ArgSpec("model", "Id del modelo"),
            ArgSpec("base_url", "URL base"),
            ArgSpec("api_key", "Clave (opcional)", optional=True),
            ArgSpec("api_key_env", "Variable de entorno con la clave (opcional)", optional=True),
        ),
        surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "provider-use", "Proveedores y roles", "Dejar activo un proveedor guardado",
        usage="satellite provider use",
        args=(ArgSpec("id", "Id del proveedor"),),
        surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "provider-remove", "Proveedores y roles", "Eliminar un proveedor guardado",
        args=(ArgSpec("id", "Id del proveedor"),),
        surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "roles", "Proveedores y roles",
        "Roles: orchestrator, context, agents, creator",
        surfaces=("cli", "desktop"),
    ),

    # ---------------------------------------------- Seguridad y diagnóstico
    CommandSpec(
        "policy", "Seguridad y diagnóstico", "Política de seguridad y modo seguro",
        surfaces=("desktop",),
    ),
    CommandSpec(
        "mode", "Seguridad y diagnóstico", "Cambiar la política de aprobación",
        usage="/mode <ask|auto|unrestricted>",
        args=(ArgSpec("modo", "ask | auto | unrestricted"),),
        surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "safe-mode", "Seguridad y diagnóstico", "Arranque sólo con agentes base",
        usage="/safe-mode <on|off>",
        args=(ArgSpec("valor", "on | off"),),
        surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "doctor", "Seguridad y diagnóstico", "Diagnóstico de integridad",
        usage="/doctor", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "stats", "Seguridad y diagnóstico", "Métricas de skills y caché",
        usage="/stats", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "setup", "Seguridad y diagnóstico", "Detectar Docker (sandbox N1)",
        usage="/setup", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "init", "Seguridad y diagnóstico", "Inicializar o reparar el proyecto",
        usage="satellite init", surfaces=("cli", "desktop"),
    ),
    CommandSpec(
        "reset", "Seguridad y diagnóstico", "Limpia agentes creados (y skills con --factory)",
        usage="/reset [--factory]", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "snapshots", "Seguridad y diagnóstico", "Listar snapshots",
        usage="/snapshots", surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "restore", "Seguridad y diagnóstico", "Restaurar desde un snapshot",
        usage="/restore <nombre>",
        args=(ArgSpec("nombre", "Nombre del snapshot"),),
        surfaces=("tui", "cli", "desktop"),
    ),

    # --------------------------------------------------------------- Aplicación
    CommandSpec(
        "config", "Aplicación", "Muestra la configuración (sin secretos)",
        usage="/config", surfaces=("tui", "cli", "desktop"),
    ),
    CommandSpec(
        "commands", "Aplicación", "Ver el catálogo de comandos y sus huecos",
        usage="satellite commands", surfaces=("cli", "desktop"),
    ),
    # La TUI es la otra interfaz: la app gráfica no puede abrirla por ti. No se esconde:
    # se marca `terminal_only` para que la paleta la muestre con el comando exacto y así
    # sea accesible de forma honesta (ver el caso "tui" en la app).
    CommandSpec(
        "tui", "Aplicación", "Abrir la interfaz de terminal (TUI)",
        usage="satellite tui", surfaces=("cli", "desktop"), terminal_only=True,
    ),
    CommandSpec(
        "shortcuts", "Aplicación", "Atajos de teclado", surfaces=("desktop",),
    ),
    CommandSpec(
        "onboarding", "Aplicación", "Asistente inicial: proyecto y proveedor",
        surfaces=("desktop",),
    ),
    CommandSpec(
        "clear", "Aplicación", "Limpiar la actividad en pantalla",
        usage="/clear", surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "help", "Aplicación", "Muestra la ayuda",
        usage="/help", surfaces=("tui", "desktop"),
    ),
    CommandSpec(
        "quit", "Aplicación", "Salir de la interfaz",
        usage="/quit", surfaces=("tui", "desktop"),
    ),
)

_BY_ID = {spec.id: spec for spec in CATALOG}


def get(command_id: str) -> CommandSpec | None:
    """Comando por id (``None`` si no existe)."""
    return _BY_ID.get(command_id)


def for_surface(surface: str) -> tuple[CommandSpec, ...]:
    """Comandos de paleta de una superficie, en el orden del catálogo."""
    return tuple(spec for spec in CATALOG if spec.in_surface(surface))


def ids_for_surface(surface: str) -> list[str]:
    return [spec.id for spec in for_surface(surface)]


def parity() -> dict:
    """Huecos de paridad: qué acciones no están como comando de paleta en cada superficie.

    ``gaps[superficie]`` = ids que no aparecen en la paleta de esa superficie. Es el
    informe que pide el criterio (detectar huecos sin taparlos llenando la pantalla de
    botones); no es una prueba.
    """
    return {
        "commands": len(CATALOG),
        "per_surface": {
            surface: sum(1 for spec in CATALOG if spec.in_surface(surface))
            for surface in SURFACES
        },
        "gaps": {
            surface: sorted(spec.id for spec in CATALOG if not spec.in_surface(surface))
            for surface in SURFACES
        },
    }
