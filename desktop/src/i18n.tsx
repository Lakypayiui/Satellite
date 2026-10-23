/**
 * Internacionalización de la interfaz de escritorio (español / inglés).
 *
 * Sistema tipado y mantenible: el diccionario declara **todas** las claves con sus dos
 * idiomas; `Clave` es la unión de claves, así que una clave inexistente es un error de
 * compilación y `tests/test_i18n.py` comprueba que no falte ningún idioma. No se traducen
 * el contenido de la conversación ni los textos técnicos desconocidos del backend.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { DEFAULT_IDIOMA, isIdioma, type IdiomaId } from "../electron/themes";
import { ApiError, bridge, mensajeDe } from "./api";

interface Par {
  es: string;
  en: string;
}

/** Diccionario único. `es` es la fuente; `en` es obligatoria para cada clave. */
export const TEXTOS = {
  "app.loading": { es: "cargando…", en: "loading…" },
  "common.back": { es: "volver", en: "back" },
  "common.close": { es: "cerrar", en: "close" },
  "common.cancel": { es: "cancelar", en: "cancel" },
  "common.save": { es: "guardar", en: "save" },
  "common.yes": { es: "sí, adelante", en: "yes, go ahead" },
  "common.retry": { es: "reintentar", en: "retry" },

  // Cabecera: estado, píldoras y acciones.
  "chip.runtime.ready": { es: "runtime listo", en: "runtime ready" },
  "chip.runtime.down": { es: "runtime caído", en: "runtime down" },
  "chip.runtime.alert": { es: "runtime alerta", en: "runtime warning" },
  "chip.runtime.unavailable": { es: "runtime no disponible", en: "runtime unavailable" },
  "chip.runtime.title": { es: "estado del runtime", en: "runtime status" },
  "chip.model.title": {
    es: "proveedor y modelo activos",
    en: "active provider and model",
  },
  "chip.model.none": { es: "sin proveedor", en: "no provider" },
  "chip.model.noneModel": { es: "sin modelo", en: "no model" },
  "chip.sandbox.title": {
    es: "nivel de aislamiento de las herramientas",
    en: "tool isolation level",
  },
  "chip.sandbox": { es: "sandbox {n}", en: "sandbox {n}" },
  "chip.context.title": {
    es: "{tokens} tokens en uso · ventana {window} ({source})",
    en: "{tokens} tokens in use · window {window} ({source})",
  },
  "chip.context": { es: "contexto {porcentaje}%", en: "context {porcentaje}%" },
  "chip.permissions": { es: "permisos {n}", en: "permissions {n}" },
  "chip.permissions.title": {
    es: "ir al permiso pendiente",
    en: "go to the pending permission",
  },
  "chip.commands": { es: "Comandos", en: "Commands" },
  "chip.commands.title": {
    es: "Ctrl+K · abre la paleta de comandos",
    en: "Ctrl+K · opens the command palette",
  },
  "chip.commands.aria": {
    es: "abrir la paleta de comandos",
    en: "open the command palette",
  },
  "chip.git.title": {
    es: "rama {rama}",
    en: "branch {rama}",
  },

  // Avisos de la cabecera/estado.
  "banner.blocked": {
    es: "sin proyecto elegido: la ejecución está bloqueada para no trabajar sobre la carpeta de Satellite. Abre una carpeta (o elige Satellite a propósito).",
    en: "no project selected: execution is blocked so it does not work on the Satellite folder. Open a folder (or pick Satellite on purpose).",
  },
  "banner.openWizard": { es: "abrir asistente", en: "open wizard" },
  "banner.backendDown": { es: "backend caído", en: "backend down" },
  "banner.runtimeErrorFallback": {
    es: "el runtime no está disponible",
    en: "the runtime is not available",
  },
  "banner.configureProvider": { es: "configurar proveedor", en: "configure provider" },

  // Barra de título.
  "titlebar.minimize": { es: "Minimizar", en: "Minimize" },
  "titlebar.maximize": { es: "Maximizar", en: "Maximize" },
  "titlebar.restore": { es: "Restaurar", en: "Restore" },
  "titlebar.close": { es: "Cerrar", en: "Close" },
  "titlebar.controls": { es: "Controles de ventana", en: "Window controls" },

  // Navegación lateral y panel de inspector.
  "tabs.activity": { es: "actividad", en: "activity" },
  "tabs.changes": { es: "cambios", en: "changes" },
  "tabs.git": { es: "git", en: "git" },
  "tabs.tasks": { es: "tareas", en: "tasks" },
  "tabs.jobs": { es: "trabajos", en: "jobs" },
  "tabs.context": { es: "contexto", en: "context" },

  // Paleta de comandos.
  "palette.title": { es: "paleta de comandos", en: "command palette" },
  "palette.open": { es: "abrir la paleta", en: "open the palette" },

  // Ajustes: categorías.
  "settings.title": { es: "Ajustes", en: "Settings" },
  "cat.modelos": { es: "Modelos y proveedores", en: "Models and providers" },
  "cat.roles": { es: "Roles", en: "Roles" },
  "cat.permisos": { es: "Permisos y sandbox", en: "Permissions and sandbox" },
  "cat.escritorio": { es: "Control de escritorio", en: "Desktop control" },
  "cat.mantenimiento": { es: "Diagnóstico y mantenimiento", en: "Diagnostics and maintenance" },
  "cat.avanzado": { es: "Avanzado", en: "Advanced" },
  "cat.apariencia": { es: "Apariencia", en: "Appearance" },
  "cat.modelos.resumen": { es: "guardados, activo y esfuerzo", en: "saved, active and effort" },
  "cat.roles.resumen": { es: "proveedor y modelo por rol", en: "provider and model per role" },
  "cat.permisos.resumen": { es: "política y aislamiento", en: "policy and isolation" },
  "cat.escritorio.resumen": {
    es: "permiso, visión y límites",
    en: "permission, vision and limits",
  },
  "cat.mantenimiento.resumen": {
    es: "doctor, métricas, herramientas y snapshots",
    en: "doctor, metrics, tools and snapshots",
  },
  "cat.avanzado.resumen": {
    es: "configuración efectiva (sin secretos)",
    en: "effective configuration (no secrets)",
  },
  "cat.apariencia.resumen": { es: "tema visual y contraste", en: "visual theme and contrast" },

  // Ajustes: idioma.
  "settings.language": { es: "Idioma", en: "Language" },
  "settings.language.note": {
    es: "se aplica al instante y se guarda en la configuración del usuario. No cambia formatos ni zonas horarias.",
    en: "applied instantly and saved to the user configuration. It does not change formats or time zones.",
  },
  "settings.appearance.theme": { es: "Tema visual", en: "Theme" },
  "settings.appearance.watermark": { es: "Marca de agua en los chats", en: "Chat watermark" },
  "settings.appearance.watermark.show": {
    es: "mostrar el logo de Satellite en los chats",
    en: "show the Satellite logo in chats",
  },
  "settings.appearance.watermark.opacity": {
    es: "opacidad de la marca de agua",
    en: "watermark opacity",
  },

  // Chat.
  "chat.welcome": { es: "Empecemos", en: "Let's start" },
  "chat.welcome.project": { es: "Empecemos en {proyecto}", en: "Let's start in {proyecto}" },
  "chat.loadingProject": { es: "cargando proyecto…", en: "loading project…" },
  "chat.composer.placeholder": {
    es: "escribe el objetivo…  (@ contexto · / comandos)",
    en: "write the goal…  (@ context · / commands)",
  },
  "chat.send": { es: "enviar", en: "send" },
  "chat.cancel": { es: "cancelar el turno", en: "cancel the turn" },
  "chat.welcome.note": {
    es: "Describe el objetivo y el agente lo ejecuta en este proyecto. Nada se envía solo: estas ideas sólo rellenan el compositor.",
    en: "Describe the goal and the agent runs it in this project. Nothing is sent on its own: these ideas only fill in the composer.",
  },
  "chat.blocked.note": {
    es: "Sin proyecto elegido la ejecución está bloqueada: elige una carpeta para empezar (ahora mismo se trabajaría sobre la carpeta de Satellite).",
    en: "With no project chosen, execution is blocked: choose a folder to start (right now it would work on the Satellite folder).",
  },
  "chat.chooseProject": { es: "Elegir proyecto…", en: "Choose project…" },
  "chat.suggestion.project": { es: "Resume este proyecto", en: "Summarise this project" },
  "chat.suggestion.start": { es: "¿Por dónde empiezo?", en: "Where do I start?" },
  "chat.suggestion.changes": { es: "Revisa los cambios recientes", en: "Review the recent changes" },
  "chat.suggestion.tests": { es: "Propón pruebas para…", en: "Propose tests for…" },
  "chat.hint.context": {
    es: "adjunta contexto del proyecto (archivos y carpetas)",
    en: "attaches project context (files and folders)",
  },
  "chat.hint.commands": {
    es: "abre un comando del catálogo compartido",
    en: "opens a command from the shared catalogue",
  },
  "chat.hint.plan": {
    es: "propone alternativas antes de tocar nada",
    en: "proposes alternatives before touching anything",
  },
  "chat.composer.aria": { es: "objetivo para el agente", en: "goal for the agent" },
  "chat.enqueue": { es: "encolar", en: "queue" },
  "chat.send.title": { es: "Enter para enviar", en: "Enter to send" },
  "chat.send.queued.title": {
    es: "El turno está en curso: esta instrucción se ejecutará al terminar",
    en: "The turn is running: this instruction will run when it finishes",
  },
  "chat.cancel.title": { es: "Corta el turno en curso", en: "Cuts the current turn" },
  "chat.plan.toggle": { es: "modo plan", en: "plan mode" },
  "chat.plan.toggle.title": {
    es: "Propone un plan antes de tocar nada",
    en: "Proposes a plan before touching anything",
  },
  "chat.policy.label": { es: "permisos", en: "permissions" },
  "chat.policy.aria": { es: "modo de permisos", en: "permission mode" },
  "chat.policy.title": {
    es: "ask pregunta cada permiso; auto aprueba lo de bajo riesgo; unrestricted no pregunta nada",
    en: "ask asks for every permission; auto approves low-risk ones; unrestricted asks nothing",
  },
  "chat.policy.ask": { es: "ask", en: "ask" },
  "chat.policy.auto": { es: "auto", en: "auto" },
  "chat.policy.unrestricted": { es: "unrestricted", en: "unrestricted" },
  "chat.status.planning": { es: "planificando", en: "planning" },
  "chat.status.reconnecting": {
    es: "reconectando con el proveedor",
    en: "reconnecting to the provider",
  },
  "chat.status.hint": {
    es: "enter envía · shift+enter salta línea",
    en: "enter sends · shift+enter adds a line",
  },
  "chat.status.busy": { es: "turno en curso", en: "turn running" },
  "chat.status.blocked": {
    es: "sin proyecto elegido · no se envía",
    en: "no project chosen · nothing is sent",
  },
  "chat.activity.summary": {
    es: "actividad del turno · {n} paso(s)",
    en: "turn activity · {n} step(s)",
  },
  "chat.activity.tool": { es: "herramienta", en: "tool" },
  "chat.permission.command": {
    es: "Permiso para ejecutar un comando",
    en: "Permission to run a command",
  },
  "chat.permission.path": {
    es: "Permiso para acceder a una ruta",
    en: "Permission to access a path",
  },
  "chat.permission.contextPath": {
    es: "Leer fuera del proyecto (una vez)",
    en: "Read outside the project (once)",
  },
  "chat.permission.other": { es: "Permiso: {tipo}", en: "Permission: {tipo}" },
  "chat.permission.reason": { es: "motivo: {motivo}", en: "reason: {motivo}" },
  "chat.permission.once": { es: "permitir una vez", en: "allow once" },
  "chat.permission.session": { es: "permitir esta sesión", en: "allow this session" },
  "chat.permission.reject": { es: "rechazar", en: "reject" },
  "chat.permission.approve": { es: "aprobar", en: "approve" },
  "chat.permission.needsChanges": { es: "pedir cambios", en: "request changes" },
  "chat.permission.changesPlaceholder": {
    es: "¿qué debería cambiar?",
    en: "what should change?",
  },
  "chat.permission.sendChanges": { es: "enviar", en: "send" },
  "chat.question.other": { es: "otra respuesta", en: "other answer" },
  "chat.queue.aria": { es: "instrucciones encoladas", en: "queued instructions" },
  "chat.queue.header": {
    es: "En cola ({n}) · se ejecutan en orden",
    en: "Queued ({n}) · they run in order",
  },
  "chat.queue.clear": { es: "vaciar", en: "clear" },
  "chat.queue.clear.title": { es: "Vaciar la cola", en: "Clear the queue" },
  "chat.queue.run": { es: "ejecutar", en: "run" },
  "chat.queue.run.title": {
    es: "Ejecuta ahora el primer prompt de la cola",
    en: "Runs the first prompt in the queue now",
  },
  "chat.queue.edit.aria": {
    es: "editar instrucción {n} en cola",
    en: "edit queued instruction {n}",
  },
  "chat.queue.edit.title": {
    es: "Enter o salir del campo para guardar",
    en: "Enter or leave the field to save",
  },
  "chat.queue.remove.aria": {
    es: "quitar de la cola: {texto}",
    en: "remove from the queue: {texto}",
  },
  "chat.queue.remove.title": { es: "Quitar de la cola", en: "Remove from the queue" },
  "chat.mentions.aria": { es: "contexto del proyecto", en: "project context" },
  "chat.mentions.error": { es: "no pude listar el contexto:", en: "I could not list the context:" },
  "chat.mentions.empty": { es: "nada coincide con @{texto}", en: "nothing matches @{texto}" },
  "chat.mentions.folder": { es: "carpeta", en: "folder" },
  "chat.mentions.file": { es: "archivo", en: "file" },
  "chat.commands.aria": { es: "comandos disponibles", en: "available commands" },
  "chat.commands.empty": { es: "sin coincidencias para /{texto}", en: "no matches for /{texto}" },
  "chat.diagram.title": { es: "Diagrama", en: "Diagram" },
  "chat.diagram.alt": {
    es: "Diagrama generado por Satellite",
    en: "Diagram generated by Satellite",
  },
  "chat.diagram.loading": { es: "cargando diagrama…", en: "loading diagram…" },
  "chat.diagram.error": {
    es: "No pude cargar el diagrama: {error}",
    en: "I couldn't load the diagram: {error}",
  },
  "chat.diagram.notRendered": {
    es: "No se pudo renderizar el SVG; esta es la fuente Mermaid.",
    en: "The SVG could not be rendered; this is the Mermaid source.",
  },
  "chat.diagram.source": { es: "ver fuente Mermaid", en: "show Mermaid source" },
  "chat.diagram.open": { es: "abrir diagrama", en: "open diagram" },
  "chat.diagram.openHint": { es: "clic para ampliar", en: "click to enlarge" },
  "chat.diagram.viewer": { es: "visor de diagrama", en: "diagram viewer" },
  "chat.diagram.zoomIn": { es: "acercar", en: "zoom in" },
  "chat.diagram.zoomOut": { es: "alejar", en: "zoom out" },
  "chat.diagram.zoomReset": { es: "restablecer zoom", en: "reset zoom" },

  // Panel derecho (inspector: actividad, git, cambios, trabajos, agentes y skills).
  "panel.title": { es: "Inspector", en: "Inspector" },
  "panel.tab.agents": { es: "agentes", en: "agents" },
  "panel.tab.skills": { es: "skills", en: "skills" },
  "panel.tab.attention": { es: "hay cambios por revisar", en: "there are changes to review" },
  "panel.refresh": { es: "actualizar", en: "refresh" },
  "panel.inUse": { es: "En uso", en: "In use" },
  "panel.catalog": { es: "Catálogo", en: "Catalogue" },
  "panel.catalog.error": { es: "No pude leer el catálogo:", en: "I could not read the catalogue:" },
  "panel.catalog.loading": { es: "cargando el catálogo…", en: "loading the catalogue…" },
  "panel.noDetail": { es: "sin detalle", en: "no detail" },
  "panel.event.tool": { es: "herramienta", en: "tool" },
  "panel.events": { es: " · {n} evento(s)", en: " · {n} event(s)" },
  "panel.activity.empty": {
    es: "Aquí se verá lo que haga el agente: herramientas, nodos y permisos, según ocurran.",
    en: "Here you will see what the agent does: tools, nodes and permissions, as they happen.",
  },
  "panel.agent.state.waiting": { es: "en espera", en: "waiting" },
  "panel.agent.state.working": { es: "trabajando", en: "working" },
  "panel.agent.state.using": { es: "usando {herramienta}", en: "using {herramienta}" },
  "panel.agent.state.thinking": { es: "razonando", en: "thinking" },
  "panel.agent.state.done": { es: "completado", en: "completed" },
  "panel.agent.state.failed": { es: "fallido", en: "failed" },
  "panel.agent.state.denied": { es: "permiso denegado", en: "permission denied" },
  "panel.agents.inUse.note": {
    es: "Sale de los eventos reales: nodos, delegaciones y herramientas. El catálogo de abajo no cuenta como uso.",
    en: "It comes from the real events: nodes, delegations and tools. The catalogue below does not count as usage.",
  },
  "panel.agents.empty": {
    es: "Ningún agente ha participado todavía.",
    en: "No agent has taken part yet.",
  },
  "panel.agents.empty.note": {
    es: "El estado sale de los eventos reales: nodos, delegaciones y herramientas.",
    en: "The state comes from the real events: nodes, delegations and tools.",
  },
  "panel.agents.detailNote": {
    es: "Detalle completo en la pestaña actividad.",
    en: "Full detail in the activity tab.",
  },
  "panel.agents.filter.placeholder": {
    es: "filtrar por id, nombre o capacidad…",
    en: "filter by id, name or capability…",
  },
  "panel.agents.filter.aria": {
    es: "filtrar agentes del catálogo",
    en: "filter catalogue agents",
  },
  "panel.agents.catalog.empty": {
    es: "El catálogo de agentes está vacío.",
    en: "The agents catalogue is empty.",
  },
  "panel.agents.catalog.noMatch": {
    es: "Ningún agente del catálogo coincide con el filtro.",
    en: "No agent in the catalogue matches the filter.",
  },
  "panel.agents.noCapabilities": { es: "sin capacidades declaradas", en: "no capabilities declared" },
  "panel.skill.state.considered": { es: "considerada", en: "considered" },
  "panel.skill.state.executed": { es: "ejecutada", en: "executed" },
  "panel.skill.state.failed": { es: "fallida", en: "failed" },
  "panel.skill.state.proposed": { es: "propuesta", en: "proposed" },
  "panel.skill.detail.searched": { es: "mirada al buscar", en: "looked at during the search" },
  "panel.skill.detail.score": { es: "score {n}", en: "score {n}" },
  "panel.skill.detail.cached": { es: "desde caché", en: "from cache" },
  "panel.skill.detail.real": { es: "ejecución real", en: "real execution" },
  "panel.skill.detail.pending": { es: "pendiente de aprobación", en: "waiting for approval" },
  "panel.skills.note.a": {
    es: "Estar en el catálogo no cuenta como uso: «considerada» sale de la búsqueda del agente y «ejecutada» de que la skill corriera de verdad. Busca en el catálogo con ",
    en: "Being in the catalogue does not count as usage: “considered” comes from the agent's search and “executed” from the skill really running. Search the catalogue with ",
  },
  "panel.skills.palette": { es: "ctrl+k · skills", en: "ctrl+k · skills" },
  "panel.skills.note.b": { es: ".", en: "." },
  "panel.skills.empty": {
    es: "Ninguna skill se ha mirado ni ejecutado aún.",
    en: "No skill has been looked at or executed yet.",
  },
  "panel.skills.search.placeholder": {
    es: "buscar por descripción (GET /skills/search)…",
    en: "search by description (GET /skills/search)…",
  },
  "panel.skills.search.aria": {
    es: "buscar skills en el catálogo",
    en: "search skills in the catalogue",
  },
  "panel.skills.editPalette": { es: "editar o borrar (ctrl+k)", en: "edit or delete (ctrl+k)" },
  "panel.skills.searchFailed": { es: "La búsqueda falló:", en: "The search failed:" },
  "panel.skills.results": { es: "Resultados de la búsqueda", en: "Search results" },
  "panel.skills.results.empty": {
    es: "Sin coincidencias en el catálogo.",
    en: "No matches in the catalogue.",
  },
  "panel.skills.noScore": { es: "sin score", en: "no score" },
  "panel.skills.score": { es: "score {n}", en: "score {n}" },
  "panel.skills.catalog.empty": {
    es: "El catálogo de skills está vacío.",
    en: "The skills catalogue is empty.",
  },
  "panel.skills.noTags": { es: "sin etiquetas", en: "no tags" },
  "panel.changes.noTasks": {
    es: "Todavía no hay tareas guardadas.",
    en: "There are no saved tasks yet.",
  },
  "panel.changes.task": { es: "Tarea", en: "Task" },
  "panel.changes.chooseTask": { es: "elegir tarea…", en: "choose a task…" },
  "panel.changes.files": { es: " · {n} archivo(s)", en: " · {n} file(s)" },
  "panel.changes.filesCount": { es: "{n} archivo(s)", en: "{n} file(s)" },
  "panel.changes.hideDiff": { es: "ocultar diff", en: "hide diff" },
  "panel.changes.showDiff": { es: "ver diff", en: "view diff" },
  "panel.changes.restore": { es: "restaurar…", en: "restore…" },
  "panel.change.new": { es: "nuevo · lo creó esta tarea", en: "new · this task created it" },
  "panel.change.noBackup": {
    es: "sin copia previa · no se puede deshacer ni ver el diff",
    en: "no previous copy · it cannot be undone or diffed",
  },
  "panel.change.modified": { es: "modificado", en: "modified" },
  "panel.diff.aria": { es: "diff con comentarios", en: "diff with comments" },
  "panel.diff.line.title": {
    es: "clic para anclar; shift+clic para extender el rango",
    en: "click to anchor; shift+click to extend the range",
  },
  "panel.diff.comment.lines": { es: "líneas", en: "lines" },
  "panel.diff.comment.line": { es: "línea {n}", en: "line {n}" },
  "panel.diff.comment.range": { es: "líneas {desde}–{hasta}", en: "lines {desde}–{hasta}" },
  "panel.diff.comment.resolved": { es: " · resuelto", en: " · resolved" },
  "panel.diff.commentIn": { es: "Comentario en {archivo}", en: "Comment on {archivo}" },
  "panel.diff.comment.placeholder": { es: "escribe el comentario…", en: "write the comment…" },
  "panel.diff.comment.aria": { es: "texto del comentario", en: "comment text" },
  "panel.diff.comment.add": { es: "añadir comentario", en: "add comment" },
  "panel.diff.comment.delete": { es: "borrar", en: "delete" },
  "panel.diff.comment.resolve": { es: "resolver", en: "resolve" },
  "panel.comment.reopen": { es: "reabrir", en: "reopen" },
  "panel.diff.commentsError": { es: "Comentarios:", en: "Comments:" },
  "panel.diff.commentsSummary": {
    es: "Comentarios ({n} sin resolver)",
    en: "Comments ({n} unresolved)",
  },
  "panel.diff.comments.note": {
    es: "Anclados a la línea del diff y guardados: sobreviven recargas.",
    en: "Anchored to the diff line and saved: they survive reloads.",
  },
  "panel.diff.manualAnchor": { es: "Anclar comentario (teclado)", en: "Anchor a comment (keyboard)" },
  "panel.diff.manualAnchor.note": {
    es: "Con ratón: clic en una línea del diff (shift+clic extiende el rango).",
    en: "With the mouse: click a diff line (shift+click extends the range).",
  },
  "panel.diff.field.file": { es: "archivo", en: "file" },
  "panel.diff.field.file.aria": { es: "archivo del comentario", en: "comment file" },
  "panel.diff.field.start": { es: "línea inicial", en: "start line" },
  "panel.diff.field.start.aria": {
    es: "línea inicial del comentario",
    en: "comment start line",
  },
  "panel.diff.field.end": { es: "línea final", en: "end line" },
  "panel.diff.field.end.aria": { es: "línea final del comentario", en: "comment end line" },
  "panel.diff.useAnchor": { es: "usar este ancla", en: "use this anchor" },
  "panel.git.loading": { es: "cargando el estado de git…", en: "loading the git status…" },
  "panel.git.notRepo": {
    es: "Este proyecto no es un repositorio git.",
    en: "This project is not a git repository.",
  },
  "panel.git.branch": { es: "rama {rama}", en: "branch {rama}" },
  "panel.git.detached": { es: "(detached)", en: "(detached)" },
  "panel.git.noUpstream": { es: "sin upstream", en: "no upstream" },
  "panel.git.runningIn": { es: "ejecutando en {destino}", en: "running in {destino}" },
  "panel.git.theProject": { es: "el proyecto", en: "the project" },
  "panel.git.clean": {
    es: "Sin cambios en el working tree.",
    en: "No changes in the working tree.",
  },
  "panel.git.conflicts": { es: "Conflictos", en: "Conflicts" },
  "panel.git.staged": { es: "Preparados (staged)", en: "Staged" },
  "panel.git.unstaged": { es: "Sin preparar", en: "Unstaged" },
  "panel.git.untracked": { es: "Sin seguimiento", en: "Untracked" },
  "panel.git.code": { es: "código", en: "code" },
  "panel.git.conflict": { es: " · conflicto", en: " · conflict" },
  "panel.git.unstage": { es: "quitar del stage", en: "unstage" },
  "panel.git.stage": { es: "preparar", en: "stage" },
  "panel.git.markResolved": { es: "marcar resuelto", en: "mark as resolved" },
  "panel.git.commit": { es: "Commit", en: "Commit" },
  "panel.git.commit.placeholder": { es: "mensaje del commit…", en: "commit message…" },
  "panel.git.commit.aria": { es: "mensaje de commit", en: "commit message" },
  "panel.git.commit.action": { es: "commit", en: "commit" },
  "panel.git.commit.note": {
    es: "No hace push ni toca remotos.",
    en: "It does not push or touch remotes.",
  },
  "panel.git.worktrees": { es: "Worktrees", en: "Worktrees" },
  "panel.worktree.name.placeholder": {
    es: "nombre (p. ej. feature-x)…",
    en: "name (e.g. feature-x)…",
  },
  "panel.worktree.name.aria": { es: "nombre del worktree", en: "worktree name" },
  "panel.worktree.create": { es: "crear worktree", en: "create worktree" },
  "panel.worktree.useProject": { es: "usar el proyecto", en: "use the project" },
  "panel.worktree.note.a": { es: "Los worktrees viven en ", en: "Worktrees live in " },
  "panel.worktree.note.b": {
    es: ". Al activar uno, el agente escribe y ejecuta ahí, no en el proyecto.",
    en: ". When one is active, the agent writes and runs there, not in the project.",
  },
  "panel.worktree.noGit": { es: "Sin git no hay worktrees.", en: "No git, no worktrees." },
  "panel.worktree.main": { es: " · principal", en: " · main" },
  "panel.worktree.locked": { es: " · bloqueado", en: " · locked" },
  "panel.worktree.prunable": { es: " · prunable", en: " · prunable" },
  "panel.worktree.dirty": {
    es: " · con cambios sin confirmar",
    en: " · with uncommitted changes",
  },
  "panel.worktree.active": { es: "activo", en: "active" },
  "panel.worktree.activate": { es: "activar", en: "activate" },
  "panel.worktree.remove": { es: "quitar…", en: "remove…" },
  "panel.worktree.removeForce": { es: "forzar quitar…", en: "force remove…" },
  "panel.jobs.background": { es: "En segundo plano", en: "In the background" },
  "panel.jobs.empty.a": {
    es: "Ninguno en marcha. El agente los lanza con ",
    en: "None running. The agent launches them with ",
  },
  "panel.jobs.empty.b": { es: ".", en: "." },
  "panel.jobs.viewOutput": { es: "ver salida", en: "view output" },
  "panel.jobs.stop": { es: "detener", en: "stop" },
  "panel.jobs.noOutput": { es: "(sin salida todavía)", en: "(no output yet)" },
  "panel.job.running": { es: "en marcha", en: "running" },
  "panel.job.stopped": { es: "detenido", en: "stopped" },
  "panel.job.done": { es: "terminado", en: "done" },
  "panel.job.failed": { es: "fallido", en: "failed" },
  "panel.job.unknown": { es: "desconocido", en: "unknown" },

  // Barra lateral.
  "sidebar.project": { es: "Proyecto", en: "Project" },
  "sidebar.noProject": { es: "sin proyecto", en: "no project" },
  "sidebar.changeFolder": { es: "cambiar carpeta…", en: "change folder…" },
  "sidebar.openFolder.title": {
    es: "Abrir otra carpeta de proyecto",
    en: "Open another project folder",
  },
  "sidebar.conversations": { es: "Conversaciones", en: "Conversations" },
  "sidebar.conversations.aria": { es: "conversaciones", en: "conversations" },
  "sidebar.newChat": { es: "Nueva conversación", en: "New conversation" },
  "sidebar.newChat.title": { es: "Ctrl+N · nueva conversación", en: "Ctrl+N · new conversation" },
  "sidebar.empty": {
    es: "Sin conversaciones activas. Crea una y escribe el objetivo.",
    en: "No active conversations. Create one and write the goal.",
  },
  "sidebar.rename.aria": { es: "nombre de la conversación", en: "conversation name" },
  "sidebar.rename.hint": { es: "doble clic para renombrar", en: "double-click to rename" },
  "sidebar.rename": { es: "Renombrar", en: "Rename" },
  "sidebar.pinned": { es: "fijada", en: "pinned" },
  "sidebar.turns": { es: "{n} turno(s)", en: "{n} turn(s)" },
  "sidebar.actions": { es: "acciones", en: "actions" },
  "sidebar.actionsOf": { es: "acciones de {nombre}", en: "actions for {nombre}" },
  "sidebar.pin": { es: "Fijar", en: "Pin" },
  "sidebar.unpin": { es: "Dejar de fijar", en: "Unpin" },
  "sidebar.archive": { es: "Archivar", en: "Archive" },
  "sidebar.unarchive": { es: "Desarchivar", en: "Unarchive" },
  "sidebar.delete": { es: "Borrar", en: "Delete" },
  "sidebar.deleteTitle": {
    es: "Borrar la conversación «{nombre}»",
    en: "Delete the conversation “{nombre}”",
  },
  "sidebar.deleteDetail": {
    es: "Se eliminará esta conversación y sus turnos guardados. No se puede deshacer.",
    en: "This conversation and its saved turns will be deleted. It cannot be undone.",
  },
  "sidebar.deleted": {
    es: "conversación «{nombre}» borrada",
    en: "conversation “{nombre}” deleted",
  },
  "sidebar.archived": { es: "Archivadas ({n})", en: "Archived ({n})" },
  "sidebar.archived.note": {
    es: "Archivar no borra nada: la conversación y sus turnos siguen guardados aquí.",
    en: "Archiving deletes nothing: the conversation and its turns stay saved here.",
  },
  "sidebar.recent": { es: "Recientes ({n})", en: "Recent ({n})" },
  "sidebar.provider": { es: "Proveedor", en: "Provider" },
  "sidebar.noKey": { es: "sin clave", en: "no key" },
  "sidebar.showPanel": { es: "Ctrl+B · mostrar el panel", en: "Ctrl+B · show the panel" },
  "sidebar.hidePanel": { es: "Ctrl+B · ocultar el panel", en: "Ctrl+B · hide the panel" },
  "sidebar.showPanel.aria": { es: "mostrar el panel izquierdo", en: "show the left panel" },
  "sidebar.hidePanel.aria": { es: "ocultar el panel izquierdo", en: "hide the left panel" },
  "sidebar.settings.title": { es: "Ajustes (Ctrl+,)", en: "Settings (Ctrl+,)" },
  "sidebar.settings.aria": { es: "abrir ajustes", en: "open settings" },

  // Paleta de comandos.
  "palette.optional": { es: " (opcional)", en: " (optional)" },
  "palette.search.placeholder": { es: "Comando…", en: "Command…" },
  "palette.search.aria": { es: "buscar comando", en: "search command" },
  "palette.catalogError": {
    es: "No pude leer el catálogo de comandos:",
    en: "I could not read the command catalogue:",
  },
  "palette.loading": { es: "cargando comandos…", en: "loading commands…" },
  "palette.noMatches": { es: "Sin coincidencias.", en: "No matches." },
  "palette.needsRuntime": { es: "requiere runtime", en: "requires runtime" },
  "palette.terminalOnly": { es: "solo terminal", en: "terminal only" },
  "palette.missing": { es: "falta: {campos}", en: "missing: {campos}" },
  "palette.run": { es: "ejecutar", en: "run" },
  "palette.footer.form": { es: " enter avanza · esc vuelve", en: " enter advances · esc goes back" },
  "palette.footer.list": {
    es: "↑↓ elegir · Enter ejecutar · Esc cerrar · las acciones destructivas piden confirmación",
    en: "↑↓ choose · Enter run · Esc close · destructive actions ask for confirmation",
  },

  // Errores propios de la UI (códigos conocidos del backend; el resto se deja igual).
  "error.backend_down": { es: "el backend no está en marcha", en: "the backend is not running" },
  "error.unauthorized": { es: "no autorizado", en: "unauthorized" },
  "error.forbidden_path": { es: "ruta no permitida", en: "path not allowed" },
  "error.project_not_found": { es: "no existe el proyecto", en: "project not found" },
  "error.provider_not_found": { es: "no existe el proveedor", en: "provider not found" },
  "error.invalid_provider": {
    es: "proveedor inválido: revisa nombre, modelo y URL",
    en: "invalid provider: check the name, model and URL",
  },
  "error.invalid_effort": {
    es: "el modelo no admite ese nivel de esfuerzo",
    en: "the model does not accept that effort level",
  },
  "error.invalid_think": {
    es: "el modelo no declara canal Think",
    en: "the model does not declare a Think channel",
  },
  "error.runtime_unavailable": {
    es: "el runtime no está disponible",
    en: "the runtime is not available",
  },
  "error.session_not_found": { es: "no existe la conversación", en: "conversation not found" },
  "error.task_not_found": { es: "no existe la tarea", en: "task not found" },
  "error.invalid_request": { es: "petición inválida", en: "invalid request" },
  "error.invalid_decision": { es: "decisión inválida", en: "invalid decision" },

  // Comunes compartidos por Ajustes y el editor de modelo.
  "common.mostUsed": { es: "  · más usado", en: "  · most used" },

  // ---------------------------------------------------------------- Ajustes
  // Cabecera, migas y grid de categorías.
  "settings.subtitle": {
    es: "proveedores, roles, permisos, escritorio, mantenimiento y apariencia",
    en: "providers, roles, permissions, desktop, maintenance and appearance",
  },
  "settings.categories.aria": { es: "categorías de ajustes", en: "settings categories" },
  "settings.breadcrumb.aria": { es: "ruta de navegación", en: "breadcrumb" },
  "settings.header.back": { es: "← Ajustes", en: "← Settings" },
  "settings.header.back.title": { es: "Esc · volver a Ajustes", en: "Esc · back to Settings" },
  "settings.header.close": { es: "← Volver", en: "← Back" },
  "settings.header.close.title": {
    es: "Esc · volver a la conversación",
    en: "Esc · back to the conversation",
  },

  // Ajustes · Modelos y proveedores.
  "settings.models.note": {
    es: "El editor de modelo es una página completa: no se abre como diálogo. La clave nunca se devuelve; sólo se sabe si está puesta.",
    en: "The model editor is a full page: it does not open as a dialog. The key is never returned; only whether it is set is known.",
  },
  "settings.models.add": { es: "Agregar modelo", en: "Add model" },
  "settings.models.chatgpt.title": { es: "Cuenta de ChatGPT", en: "ChatGPT account" },
  "settings.models.chatgpt.note": {
    es: "Conecta ChatGPT para usar los modelos autorizados por tu plan. Satellite guardará la sesión cifrada en este dispositivo.",
    en: "Connect ChatGPT to use models authorized by your plan. Satellite stores the session encrypted on this device.",
  },
  "settings.models.chatgpt.signIn": { es: "Iniciar sesión con ChatGPT", en: "Sign in with ChatGPT" },
  "settings.models.chatgpt.connectedAs": { es: "Conectado como {email}", en: "Connected as {email}" },
  "settings.models.chatgpt.useModel": { es: "Usar este modelo", en: "Use this model" },
  "settings.models.chatgpt.disconnect": { es: "Desconectar", en: "Disconnect" },
  "settings.models.chatgpt.connected": { es: "cuenta de ChatGPT conectada", en: "ChatGPT account connected" },
  "settings.models.chatgpt.disconnected": { es: "cuenta de ChatGPT desconectada", en: "ChatGPT account disconnected" },
  "settings.models.chatgpt.saved": { es: "proveedor ChatGPT agregado con {model}", en: "ChatGPT provider added with {model}" },
  "settings.models.claude.title": { es: "Cuenta de Claude", en: "Claude account" },
  "settings.models.claude.note": {
    es: "Claude Code usa el inicio de sesión oficial de tu plan Claude. Inicia sesión en la terminal y después agrega un modelo para asignarlo a los roles.",
    en: "Claude Code uses the official sign-in for your Claude plan. Sign in in the terminal, then add a model and assign it to roles.",
  },
  "settings.models.claude.signIn": { es: "Iniciar sesión con Claude", en: "Sign in with Claude" },
  "settings.models.claude.useModel": { es: "Agregar este modelo", en: "Add this model" },
  "settings.models.claude.installRequired": { es: "Instala Claude Code para habilitar modelos de tu cuenta.", en: "Install Claude Code to enable models from your account." },
  "settings.models.claude.terminalMissing": { es: "No se encontró una terminal disponible.", en: "No terminal was found." },
  "settings.models.claude.loginOpened": { es: "Claude Code se abrió en una terminal. Completa el inicio de sesión allí.", en: "Claude Code opened in a terminal. Complete sign-in there." },
  "settings.models.claude.saved": { es: "proveedor Claude agregado con {model}", en: "Claude provider added with {model}" },
  "settings.models.saved": { es: "Guardados", en: "Saved" },
  "settings.models.empty": {
    es: "Todavía no hay ningún proveedor guardado.",
    en: "There is no saved provider yet.",
  },
  "settings.models.edit": { es: "editar", en: "edit" },
  "settings.models.active": { es: "activo", en: "active" },
  "settings.models.remove": { es: "Quitar", en: "Remove" },
  "settings.models.remove.title": {
    es: "Quitar el proveedor «{proveedor}»",
    en: "Remove the provider “{proveedor}”",
  },
  "settings.models.remove.detail": {
    es: "Se borra de la lista de proveedores guardados. Los demás no se tocan.",
    en: "It is deleted from the list of saved providers. The others are left untouched.",
  },
  "settings.models.removed": {
    es: "proveedor «{proveedor}» eliminado",
    en: "provider “{proveedor}” deleted",
  },
  "settings.models.none": { es: "ninguno", en: "none" },
  "settings.models.effort.title": {
    es: "esfuerzo de razonamiento (auto = lo decide el proveedor)",
    en: "reasoning effort (auto = the provider decides)",
  },
  "settings.models.effort.aria": { es: "esfuerzo de {proveedor}", en: "effort for {proveedor}" },
  "settings.models.effort.none.title": {
    es: "este modelo no declara niveles de esfuerzo",
    en: "this model does not declare effort levels",
  },
  "settings.models.effort.na": { es: "esfuerzo n/d", en: "effort n/a" },
  "settings.models.effort.changed": {
    es: "esfuerzo de «{proveedor}»: {esfuerzo}",
    en: "effort for “{proveedor}”: {esfuerzo}",
  },
  "settings.models.footer": {
    es: "Activo: {activo}. «esfuerzo» sólo se ofrece en modelos que lo admiten; «auto» no envía nada y lo decide el proveedor. El tope de salida y lo demás se editan por modelo con «editar».",
    en: "Active: {activo}. “effort” is only offered for models that support it; “auto” sends nothing and the provider decides. The output cap and the rest are edited per model with “edit”.",
  },
  "settings.models.meta.noActive": { es: "sin proveedor activo", en: "no active provider" },
  "settings.models.meta.count": { es: "{n} guardado(s) · {nombre}", en: "{n} saved · {nombre}" },
  "settings.models.loading": {
    es: "cargando modelos del proveedor…",
    en: "loading the provider's models…",
  },

  // Ajustes · Roles.
  "settings.roles.note": {
    es: "Cada rol se aplica por separado, con su propio botón: aplicar uno no cambia los demás. Elegir un proveedor guardado trae sus modelos; sin proveedor, el rol usa el global.",
    en: "Each role is applied separately, with its own button: applying one does not change the others. Choosing a saved provider brings its models; with no provider, the role uses the global one.",
  },
  "settings.roles.empty": {
    es: "Todavía no hay proveedores guardados: guárdalos en «Modelos y proveedores» para poder fijar un rol.",
    en: "There are no saved providers yet: save them in “Models and providers” to be able to set a role.",
  },
  "settings.roles.apply": { es: "Aplicar", en: "Apply" },
  "settings.roles.meta": {
    es: "{n} rol(es) · cada uno con su proveedor",
    en: "{n} role(s) · each with its own provider",
  },
  "settings.roles.applied": {
    es: "rol {rol} aplicado{detalle}",
    en: "role {rol} applied{detalle}",
  },
  "settings.roles.applied.effort": {
    es: " (esfuerzo {esfuerzo})",
    en: " (effort {esfuerzo})",
  },
  "settings.roles.savedNoRuntime": {
    es: "rol {rol} guardado, pero el runtime no se pudo rearmar: {error}",
    en: "role {rol} saved, but the runtime could not be re-armed: {error}",
  },
  "settings.roles.modelsError": {
    es: "no se pudieron listar los modelos",
    en: "the models could not be listed",
  },
  "settings.roles.row.subagents": {
    es: "Modelo para subagentes",
    en: "Subagent model",
  },
  "settings.roles.row.inheritsParent": {
    es: " (hereda el modelo del padre)",
    en: " (inherits the parent model)",
  },
  "settings.roles.row.inheritParentOption": {
    es: "— heredar del padre —",
    en: "— inherit from the parent —",
  },
  "settings.roles.row.usesGlobal": { es: " (usa el global)", en: " (uses the global one)" },
  "settings.roles.row.effort": { es: " · esfuerzo {esfuerzo}", en: " · effort {esfuerzo}" },
  "settings.roles.row.global": {
    es: "— el global ({proveedor}) —",
    en: "— the global one ({proveedor}) —",
  },
  "settings.roles.row.providerDefault": {
    es: "— el del proveedor —",
    en: "— the provider's own —",
  },
  "settings.roles.row.providerDefault.model": {
    es: "— el del proveedor ({modelo}) —",
    en: "— the provider's own ({modelo}) —",
  },
  "settings.roles.row.modelPlaceholder": { es: "modelo (opcional)", en: "model (optional)" },
  "settings.roles.row.refresh.title": {
    es: "volver a pedir el catálogo al proveedor",
    en: "ask the provider for the catalogue again",
  },
  "settings.roles.row.refresh.aria": {
    es: "refrescar el catálogo de modelos",
    en: "refresh the models catalogue",
  },
  "settings.roles.row.think.title": {
    es: "canal Think: {canal}",
    en: "Think channel: {canal}",
  },
  "settings.roles.row.think.none": {
    es: "este modelo no declara canal Think",
    en: "this model does not declare a Think channel",
  },
  "settings.roles.row.effort.title": {
    es: "auto = lo decide el proveedor · off = apaga el razonamiento (si se puede)",
    en: "auto = the provider decides · off = turns reasoning off (if it can)",
  },
  "settings.roles.row.effort.aria": { es: "esfuerzo de {rol}", en: "effort for {rol}" },
  "settings.roles.row.levels.loading": { es: "cargando niveles…", en: "loading levels…" },
  "settings.roles.row.levels.none": { es: "sin niveles declarados", en: "no levels declared" },
  "settings.roles.row.channel": { es: "canal: {canal}", en: "channel: {canal}" },
  "settings.roles.row.inferred": {
    es: " · niveles deducidos del nombre",
    en: " · levels inferred from the name",
  },
  "settings.roles.row.modelsError": {
    es: "no pude leer sus modelos: {error}",
    en: "I could not read its models: {error}",
  },

  // Ajustes · Permisos y sandbox.
  "settings.permissions.mode.ask": {
    es: "ask — pregunta cada permiso",
    en: "ask — asks for every permission",
  },
  "settings.permissions.mode.auto": {
    es: "auto — aprueba lo de bajo riesgo",
    en: "auto — approves the low-risk ones",
  },
  "settings.permissions.mode.unrestricted": {
    es: "unrestricted — no pregunta nada",
    en: "unrestricted — asks nothing",
  },
  "settings.permissions.safeMode": {
    es: "modo seguro (sólo agentes base y primitivas)",
    en: "safe mode (base agents and primitives only)",
  },
  "settings.permissions.meta": {
    es: "{modo} · sandbox {nivel}{seguro}",
    en: "{modo} · sandbox {nivel}{seguro}",
  },
  "settings.permissions.meta.safe": { es: " · modo seguro", en: " · safe mode" },
  "settings.permissions.sandbox.note.a": {
    es: "Sandbox {nivel}. N0 usa un subproceso y un límite de tiempo; no bloquea el acceso a la red: ",
    en: "Sandbox {nivel}. N0 uses a subprocess and a time limit; it does not block network access: ",
  },
  "settings.permissions.sandbox.note.no": { es: "no", en: "it does not" },
  "settings.permissions.sandbox.note.b": {
    es: " aísla del sistema operativo. Sólo N1 (Docker, sin red, espacio de trabajo en sólo lectura) confina de verdad; si el sandbox elegido no puede aplicarse (por ejemplo N1 sin Docker), los comandos del agente y las skills fallan cerrado en vez de correr en el host.",
    en: " isolate it from the operating system. Only N1 (Docker, no network, read-only workspace) truly confines it; if the chosen sandbox cannot be enforced (for example N1 without Docker), the agent's commands and skills fail closed instead of running on the host.",
  },
  "settings.permissions.setup": { es: "Detectar Docker (setup)", en: "Detect Docker (setup)" },

  // Ajustes · Control de escritorio (F7).
  "settings.desktop.note.a": { es: "Apagado por defecto. Activar esto ", en: "Off by default. Enabling this " },
  "settings.desktop.note.no": { es: "no concede permisos", en: "does not grant permissions" },
  "settings.desktop.note.b": {
    es: ": cada observación o acción sigue pasando por aprobación humana, grants por sesión+app, topes y auditoría. MCP no puede habilitarlo ni configurarlo.",
    en: ": every observation or action still goes through human approval, per session+app grants, caps and auditing. MCP cannot enable or configure it.",
  },
  "settings.desktop.toggle": {
    es: "habilitar control de escritorio",
    en: "enable desktop control",
  },
  "settings.desktop.allowlist.title": {
    es: "Apps permitidas",
    en: "Allowed apps",
  },
  "settings.desktop.allowlist.note": {
    es: "La política sólo puede observar o controlar apps de esta lista; la denylist local (terminales, gestores de contraseñas, ajustes del sistema) siempre gana. Sin apps aquí, toda petición se deniega.",
    en: "The policy can only observe or control apps in this list; the local denylist (terminals, password managers, system settings) always wins. With no apps here, every request is denied.",
  },
  "settings.desktop.allowlist.label": {
    es: "una app por línea (nombre del proceso)",
    en: "one app per line (process name)",
  },
  "settings.desktop.allowlist.placeholder": {
    es: "Safari\nGoogle Chrome\ncom.apple.Terminal",
    en: "Safari\nGoogle Chrome\ncom.apple.Terminal",
  },
  "settings.desktop.allowlist.hint": {
    es: "También se aceptan separadas por comas. Ejemplos: Safari, Google Chrome, Finder, notepad.",
    en: "Comma-separated is also accepted. Examples: Safari, Google Chrome, Finder, notepad.",
  },
  "settings.desktop.allowlist.save": {
    es: "guardar apps permitidas",
    en: "save allowed apps",
  },
  "settings.desktop.allowlist.saved": {
    es: "apps permitidas guardadas",
    en: "allowed apps saved",
  },
  "settings.desktop.allowlist.savedList": {
    es: "Guardado: {apps}. La política lo aplicará en la próxima petición.",
    en: "Saved: {apps}. The policy will apply it on the next request.",
  },
  "settings.desktop.allowlist.savedEmpty": {
    es: "Allowlist vacía: la política denegará todas las apps.",
    en: "Empty allowlist: the policy will deny every app.",
  },
  "settings.desktop.backend.missing": {
    es: "El backend de control no está instalado (extra {extra}). Instálalo para poder observar y controlar ventanas.",
    en: "The control backend is not installed (extra {extra}). Install it to observe and control windows.",
  },
  "settings.desktop.backend.untrusted": {
    es: "macOS no tiene permiso de Accesibilidad para este proceso: concédelo en Ajustes del Sistema → Privacidad y seguridad → Accesibilidad y reinicia la app.",
    en: "macOS has not granted Accessibility permission to this process: allow it in System Settings → Privacy & Security → Accessibility, then restart the app.",
  },
  "settings.desktop.enabled": {
    es: "control de escritorio habilitado",
    en: "desktop control enabled",
  },
  "settings.desktop.disabled": {
    es: "control de escritorio deshabilitado",
    en: "desktop control disabled",
  },
  "settings.desktop.savedNoRuntime": {
    es: "guardado, pero el runtime no se pudo rearmar: {error}",
    en: "saved, but the runtime could not be re-armed: {error}",
  },
  "settings.desktop.meta.optional": { es: "control opcional", en: "optional control" },
  "settings.desktop.meta.on": { es: "habilitado", en: "enabled" },
  "settings.desktop.meta.off": { es: "apagado", en: "off" },
  "settings.desktop.meta.none": { es: "{estado} · sin visión", en: "{estado} · no vision" },
  "settings.desktop.vision.title": {
    es: "Proveedor/modelo para el fallback de visión",
    en: "Provider/model for the vision fallback",
  },
  "settings.desktop.vision.empty": {
    es: "No hay proveedores guardados.",
    en: "There are no saved providers.",
  },
  "settings.desktop.vision.goModels": {
    es: "ir a Modelos y proveedores",
    en: "go to Models and providers",
  },
  "settings.desktop.vision.provider": { es: "proveedor", en: "provider" },
  "settings.desktop.vision.provider.aria": { es: "proveedor de visión", en: "vision provider" },
  "settings.desktop.vision.provider.saved": {
    es: "proveedor de visión guardado",
    en: "vision provider saved",
  },
  "settings.desktop.vision.noProvider": { es: "— sin proveedor —", en: "— no provider —" },
  "settings.desktop.vision.model": { es: "modelo", en: "model" },
  "settings.desktop.vision.model.aria": { es: "modelo de visión", en: "vision model" },
  "settings.desktop.vision.model.saved": {
    es: "modelo de visión guardado",
    en: "vision model saved",
  },
  "settings.desktop.vision.providerDefault": {
    es: "— el del proveedor —",
    en: "— the provider's own —",
  },
  "settings.desktop.availability": { es: "Disponibilidad", en: "Availability" },
  "settings.desktop.selected": {
    es: "Seleccionado: {proveedor} · {modelo}.",
    en: "Selected: {proveedor} · {modelo}.",
  },
  "settings.desktop.noVision": {
    es: "Sin proveedor de visión configurado.",
    en: "No vision provider configured.",
  },
  "settings.desktop.notImplemented": {
    es: "La visión real no está implementada",
    en: "Real vision is not implemented",
  },
  "settings.desktop.notImplemented.note": {
    es: " (F7: sólo interfaz; no se envían imágenes ni se llama al proveedor).",
    en: " (F7: interface only; no images are sent and the provider is not called).",
  },
  "settings.desktop.ocr": {
    es: "OCR local con cajas: interfaz definida, implementación no incluida.",
    en: "Local OCR with boxes: interface defined, implementation not included.",
  },
  "settings.desktop.guarantees": {
    es: "Garantías activas: aprobación, grants, topes y auditoría. MCP: {mcp}.",
    en: "Active guarantees: approval, grants, caps and auditing. MCP: {mcp}.",
  },
  "settings.desktop.mcp.on": { es: "habilitado", en: "enabled" },
  "settings.desktop.mcp.off": { es: "no disponible", en: "not available" },

  // Ajustes · Safe Proxy (servicio embebido, arrancado en proceso por Satellite).
  "cat.safeProxy": { es: "Safe Proxy", en: "Safe Proxy" },
  "cat.safeProxy.resumen": {
    es: "servicio embebido de saneamiento",
    en: "embedded sanitising service",
  },
  "cat.safeProxy.meta": { es: "control local", en: "local control" },
  "settings.safeProxy.note": {
    es: "Safe Proxy sanea lo que sale hacia la API del modelo (bloquea, ofusca y enmascara) y restaura la respuesta. Modo interno: se aplica dentro del propio Satellite, sin abrir ningún puerto. Modo servidor: se escucha en loopback para que otros programas apunten a él. Las opciones se aplican en caliente y se persisten.",
    en: "Safe Proxy sanitises what leaves for the model API (it blocks, obfuscates and masks) and restores the response. Internal mode: applied inside Satellite itself, opening no port. Server mode: it listens on loopback so other programs can point at it. Options apply live and persist.",
  },
  "settings.safeProxy.service": { es: "Servicio embebido", en: "Embedded service" },
  "settings.safeProxy.mode": { es: "Modo", en: "Mode" },
  "settings.safeProxy.mode.aria": { es: "modo del Safe Proxy", en: "Safe Proxy mode" },
  "settings.safeProxy.mode.internal": {
    es: "Interno (en el ciclo del proveedor, sin puerto)",
    en: "Internal (in the provider cycle, no port)",
  },
  "settings.safeProxy.mode.server": {
    es: "Servidor (loopback, para consumidores externos)",
    en: "Server (loopback, for external consumers)",
  },
  "settings.safeProxy.mode.saved": { es: "modo cambiado", en: "mode changed" },
  "settings.safeProxy.internal.note": {
    es: "No abre ningún puerto: la política se aplica en el mismo proceso, antes de enviar la petición al proveedor, y se restauran los marcadores en la respuesta. Con el interruptor apagado la petición pasa intacta (bypass).",
    en: "It opens no port: the policy is applied in-process, before the request is sent to the provider, and placeholders are restored in the response. With the switch off the request passes through untouched (bypass).",
  },
  "settings.safeProxy.internal.toggle": {
    es: "aplicar la política del proxy a las peticiones",
    en: "apply the proxy policy to requests",
  },
  "settings.safeProxy.internal.on": {
    es: "política del proxy aplicada en el ciclo del proveedor",
    en: "proxy policy applied in the provider cycle",
  },
  "settings.safeProxy.internal.off": {
    es: "política del proxy desactivada: las peticiones pasan intactas",
    en: "proxy policy disabled: requests pass through untouched",
  },
  "settings.safeProxy.state": { es: "Estado", en: "Status" },
  "settings.safeProxy.state.internal": { es: "interno", en: "internal" },
  "settings.safeProxy.state.stopped": { es: "parado", en: "stopped" },
  "settings.safeProxy.state.running": { es: "en marcha", en: "running" },
  "settings.safeProxy.state.configError": {
    es: "error de configuración",
    en: "configuration error",
  },
  "settings.safeProxy.state.portConflict": { es: "puerto ocupado", en: "port in use" },
  "settings.safeProxy.start": { es: "Encender", en: "Start" },
  "settings.safeProxy.stop": { es: "Apagar", en: "Stop" },
  "settings.safeProxy.started": { es: "servicio encendido", en: "service started" },
  "settings.safeProxy.stopped": { es: "servicio apagado", en: "service stopped" },
  "settings.safeProxy.bind": { es: "Escucha (sólo local)", en: "Listening (local only)" },
  "settings.safeProxy.port": { es: "puerto", en: "port" },
  "settings.safeProxy.port.aria": {
    es: "puerto del servicio Safe Proxy",
    en: "Safe Proxy service port",
  },
  "settings.safeProxy.host.note": {
    es: "El servicio sólo escucha en loopback (127.0.0.1); el host no es configurable, así no se expone a la red.",
    en: "The service only listens on loopback (127.0.0.1); the host is not configurable, so it is never exposed to the network.",
  },
  "settings.safeProxy.save": { es: "Guardar", en: "Save" },
  "settings.safeProxy.savedPort": { es: "puerto guardado", en: "port saved" },
  "settings.safeProxy.refresh": { es: "Refrescar", en: "Refresh" },
  "settings.safeProxy.token.note": {
    es: "Los tokens de acceso y de control los genera Satellite en el host (write-only): no se muestran, no se registran en logs y no viajan a la interfaz.",
    en: "The access and control tokens are generated by Satellite on the host (write-only): never shown, never logged and never sent to the interface.",
  },
  "settings.safeProxy.routing": { es: "Routing", en: "Routing" },
  "settings.safeProxy.toggle": { es: "routing habilitado", en: "routing enabled" },
  "settings.safeProxy.enabled": { es: "routing habilitado", en: "routing enabled" },
  "settings.safeProxy.disabled": { es: "routing deshabilitado", en: "routing disabled" },
  "settings.safeProxy.savedConfig": {
    es: "opciones del servicio actualizadas",
    en: "service options updated",
  },
  "settings.safeProxy.config.title": { es: "Opciones del servicio", en: "Service options" },
  "settings.safeProxy.config.note": {
    es: "Sólo las opciones que el servicio permite cambiar (allowlist). Se aplican en caliente y se persisten en su propio estado; bind, puerto, rutas y secretos no se tocan.",
    en: "Only the options the service allows changing (allowlist). They apply live and persist in its own state; bind, port, routes and secrets are untouched.",
  },
  "settings.safeProxy.config.allowedModels": { es: "Modelos permitidos", en: "Allowed models" },
  "settings.safeProxy.config.blockTerms": { es: "Términos bloqueados", en: "Blocked terms" },
  "settings.safeProxy.config.maskTerms": { es: "Términos enmascarados", en: "Masked terms" },
  "settings.safeProxy.config.lines.hint": { es: "uno por línea", en: "one per line" },
  "settings.safeProxy.config.pii": { es: "PII a enmascarar", en: "PII to mask" },
  "settings.safeProxy.config.secretsAction": { es: "Secretos", en: "Secrets" },
  "settings.safeProxy.config.secretsAction.block": { es: "bloquear", en: "block" },
  "settings.safeProxy.config.secretsAction.redact": { es: "enmascarar", en: "redact" },
  "settings.safeProxy.config.apiKeys": { es: "API keys", en: "API keys" },
  "settings.safeProxy.config.apiKeys.aria": {
    es: "detectar y proteger claves de API",
    en: "detect and protect API keys",
  },
  "settings.safeProxy.config.apiKeys.hint": {
    es: "detecta claves de API (sk-…, AIza…, xai-…, hf_…, gsk_…, r8-…, pplx-…) y las bloqueará o enmascarará según «Secretos»; al apagarla pasan sin tocar.",
    en: "detects API keys (sk-…, AIza…, xai-…, hf_…, gsk_…, r8-…, pplx-…) and will block or mask them per “Secrets”; when off they pass through untouched.",
  },
  "settings.safeProxy.config.otherLanguages": { es: "Otros lenguajes", en: "Other languages" },
  "settings.safeProxy.config.otherLanguages.block": { es: "bloquear", en: "block" },
  "settings.safeProxy.config.otherLanguages.allow": { es: "permitir", en: "allow" },
  "settings.safeProxy.config.obfuscate": {
    es: "Ofuscar código Python",
    en: "Obfuscate Python code",
  },
  "settings.safeProxy.config.stripComments": {
    es: "Eliminar comentarios y docstrings",
    en: "Strip comments and docstrings",
  },
  "settings.safeProxy.config.failClosed": {
    es: "Fallar cerrado (no enviar si no se puede sanear)",
    en: "Fail closed (send nothing if it cannot be sanitised)",
  },
  "settings.safeProxy.config.timeout": { es: "Timeout del upstream (s)", en: "Upstream timeout (s)" },
  "settings.safeProxy.config.timeout.aria": {
    es: "timeout del upstream en segundos",
    en: "upstream timeout in seconds",
  },
  "settings.safeProxy.config.save": { es: "Guardar opciones", en: "Save options" },
  "settings.safeProxy.meta.stopped": { es: "parado", en: "stopped" },
  "settings.safeProxy.meta.running": { es: "en marcha", en: "running" },
  "settings.safeProxy.meta.internal": { es: "interno", en: "internal" },
  "settings.safeProxy.meta.error": { es: "revisar", en: "check" },

  // Ajustes · Diagnóstico y mantenimiento.
  "settings.maintenance.doctor": { es: "Diagnóstico", en: "Diagnostics" },
  "settings.terminal.title": { es: "Terminal", en: "Terminal" },
  "settings.terminal.note": {
    es: "La app incluye la CLI y la TUI de Satellite. Instala el comando `satellite` para usarlo desde cualquier terminal (satellite run, satellite tui, satellite mcp…).",
    en: "The app includes Satellite's CLI and TUI. Install the `satellite` command to use it from any terminal (satellite run, satellite tui, satellite mcp…).",
  },
  "settings.terminal.install": { es: "Instalar comando satellite", en: "Install satellite command" },
  "settings.terminal.tui": { es: "Abrir la TUI", en: "Open the TUI" },
  "settings.terminal.failed": { es: "No se pudo abrir la terminal.", en: "Could not open the terminal." },
  "settings.maintenance.stats": { es: "Métricas", en: "Metrics" },
  "settings.maintenance.compact": { es: "Compactar contexto", en: "Compact context" },
  "settings.maintenance.init": { es: "Inicializar o reparar", en: "Initialise or repair" },
  "settings.maintenance.tools": { es: "Herramientas", en: "Tools" },
  "settings.maintenance.snapshots": { es: "Snapshots", en: "Snapshots" },
  "settings.maintenance.tools.title": {
    es: "Herramientas del runtime ({n})",
    en: "Runtime tools ({n})",
  },
  "settings.maintenance.tools.empty": { es: "El runtime no ofrece ninguna.", en: "The runtime offers none." },
  "settings.maintenance.tools.noCategories": { es: "sin categorías", en: "no categories" },
  "settings.maintenance.snapshots.title": { es: "Snapshots ({n})", en: "Snapshots ({n})" },
  "settings.maintenance.snapshots.empty": {
    es: "No hay snapshots guardados en este proyecto.",
    en: "There are no snapshots saved in this project.",
  },
  "settings.maintenance.restore": { es: "restaurar…", en: "restore…" },
  "settings.maintenance.restore.run": {
    es: "restaurar «{nombre}»",
    en: "restore “{nombre}”",
  },
  "settings.maintenance.restore.confirm.title": {
    es: "Restaurar el snapshot «{nombre}»",
    en: "Restore the snapshot “{nombre}”",
  },
  "settings.maintenance.restore.confirm.detail": {
    es: "Se descarta el estado actual del proyecto y se vuelve al del snapshot. No se puede deshacer.",
    en: "The current project state is discarded and it goes back to the snapshot's. This cannot be undone.",
  },
  "settings.maintenance.restore.note": {
    es: "Restaurar un snapshot borra el estado actual: se pide confirmación con lo que se descarta antes de tocar nada.",
    en: "Restoring a snapshot deletes the current state: confirmation is asked showing what is discarded before touching anything.",
  },
  "settings.maintenance.reset.title": { es: "Reset del estado base", en: "Reset the base state" },
  "settings.maintenance.reset.detail": {
    es: "Borra los agentes y las skills creados en este proyecto. Las skills del sistema se conservan.",
    en: "Deletes the agents and skills created in this project. The system skills are kept.",
  },
  "settings.maintenance.reset": { es: "Reset…", en: "Reset…" },
  "settings.maintenance.meta": {
    es: "doctor, métricas, herramientas, snapshots y reset",
    en: "doctor, metrics, tools, snapshots and reset",
  },

  // Ajustes · Avanzado.
  "settings.advanced.meta": {
    es: "configuración efectiva (sin secretos)",
    en: "effective configuration (no secrets)",
  },
  "settings.advanced.note.a": {
    es: "Los secretos no se muestran: cuando hay una clave guardada sólo aparece ",
    en: "Secrets are not shown: when a key is saved only ",
  },
  "settings.advanced.note.b": { es: ", nunca su valor.", en: " appears, never its value." },

  // Ajustes · Apariencia.
  "settings.appearance.note": {
    es: "El tema se aplica al instante y se guarda en la configuración del usuario: al volver a abrir la app se restaura. Por defecto, Obsidiana (el aspecto oscuro de siempre).",
    en: "The theme is applied instantly and saved to the user configuration: it is restored when the app is opened again. By default, Obsidian (the dark look as always).",
  },
  "settings.appearance.themes.aria": { es: "temas disponibles", en: "available themes" },
  "settings.appearance.light": { es: "claro", en: "light" },
  "settings.appearance.dark": { es: "oscuro", en: "dark" },
  "settings.appearance.intensity": { es: "Intensidad del blanco", en: "White intensity" },
  "settings.appearance.intensity.aria": {
    es: "Intensidad del blanco (Deep Dark)",
    en: "White intensity (Deep Dark)",
  },
  "settings.appearance.watermark.applied": {
    es: "se aplica al instante y se guarda; el mismo valor vale para temas claros y oscuros.",
    en: "applied instantly and saved; the same value works for light and dark themes.",
  },
  "settings.appearance.watermark.disabled": {
    es: "el control queda deshabilitado porque la marca está oculta; el valor se conserva.",
    en: "the control stays disabled because the watermark is hidden; the value is kept.",
  },
  "settings.appearance.watermark.opacity.aria": {
    es: "Opacidad de la marca de agua del chat",
    en: "Chat watermark opacity",
  },
  "settings.appearance.current": { es: "Tema actual: {tema}", en: "Current theme: {tema}" },
  "settings.appearance.current.intensity": { es: " · blanco {nivel}", en: " · white {nivel}" },
  "settings.appearance.current.note": {
    es: ". La barra de título y sus controles de ventana también usan los colores del tema.",
    en: ". The title bar and its window controls also use the theme colours.",
  },
  "settings.appearance.meta.theme": { es: "tema {tema}", en: "theme {tema}" },
  "settings.appearance.meta.deepdark": {
    es: "tema {tema} · blanco {nivel}",
    en: "theme {tema} · white {nivel}",
  },
  "settings.appearance.tone.light": { es: " · claro", en: " · light" },
  "settings.appearance.tone.dark": { es: " · oscuro", en: " · dark" },

  // Temas del catálogo (nombre y descripción visibles en Apariencia; el id no cambia).
  "settings.theme.obsidiana.name": { es: "Obsidiana", en: "Obsidian" },
  "settings.theme.obsidiana.description": {
    es: "Oscuro cálido, acento verde. El aspecto de siempre.",
    en: "Warm dark, green accent. The look as always.",
  },
  "settings.theme.grafito.name": { es: "Grafito", en: "Graphite" },
  "settings.theme.grafito.description": {
    es: "Gris neutro frío con acento acero.",
    en: "Cool neutral grey with a steel accent.",
  },
  "settings.theme.medianoche.name": { es: "Medianoche", en: "Midnight" },
  "settings.theme.medianoche.description": {
    es: "Azul profundo con acento eléctrico.",
    en: "Deep blue with an electric accent.",
  },
  "settings.theme.bosque.name": { es: "Bosque", en: "Forest" },
  "settings.theme.bosque.description": {
    es: "Verde profundo y acento vegetal.",
    en: "Deep green with a plant accent.",
  },
  "settings.theme.claro.name": { es: "Claro cálido", en: "Warm light" },
  "settings.theme.claro.description": {
    es: "Papel cálido con tinta oscura y acento verde.",
    en: "Warm paper with dark ink and a green accent.",
  },
  "settings.theme.deepdark.name": { es: "Deep Dark", en: "Deep Dark" },
  "settings.theme.deepdark.description": {
    es: "Negro puro en toda la app, con blanco ajustable.",
    en: "Pure black across the app, with adjustable white.",
  },

  // Intensidad del blanco (sólo Deep Dark); los ids del catálogo no cambian.
  "settings.intensity.suave.label": { es: "Suave", en: "Soft" },
  "settings.intensity.suave.hint": { es: "gris claro", en: "light grey" },
  "settings.intensity.medio.label": { es: "Medio", en: "Medium" },
  "settings.intensity.medio.hint": { es: "blanco tenue", en: "faint white" },
  "settings.intensity.brillante.label": { es: "Brillante", en: "Bright" },
  "settings.intensity.brillante.hint": { es: "casi blanco puro", en: "almost pure white" },
  "settings.intensity.maximo.label": { es: "Máximo", en: "Maximum" },
  "settings.intensity.maximo.hint": { es: "blanco puro", en: "pure white" },

  // ------------------------------------------------------- Editor de modelo
  "editor.section.general": { es: "General", en: "General" },
  "editor.section.general.summary": { es: "nombre, familia y modelo", en: "name, family and model" },
  "editor.section.connection": { es: "Conexión", en: "Connection" },
  "editor.section.connection.summary": { es: "endpoint y credencial", en: "endpoint and credential" },
  "editor.section.generation": { es: "Generación", en: "Generation" },
  "editor.section.generation.summary": {
    es: "esfuerzo y tope de salida",
    en: "effort and output cap",
  },
  "editor.section.advanced": { es: "Avanzado", en: "Advanced" },
  "editor.section.advanced.summary": {
    es: "capacidades que el runtime usa",
    en: "capabilities the runtime uses",
  },
  "editor.sections.aria": { es: "secciones del modelo", en: "model sections" },
  "editor.header.back": { es: "← Modelos", en: "← Models" },
  "editor.header.back.title": { es: "Esc · volver a Modelos", en: "Esc · back to Models" },
  "editor.global.note": {
    es: "Este proveedor y su clave quedan guardados en la configuración global y estarán disponibles para todos tus proyectos.",
    en: "This provider and its key are saved in global settings and will be available to all your projects.",
  },
  "editor.title.add": { es: "Agregar modelo", en: "Add model" },
  "editor.title.edit": { es: "Editar modelo", en: "Edit model" },
  "editor.model.none": { es: "sin modelo", en: "no model" },
  "editor.newProvider": { es: "nuevo proveedor y modelo", en: "new provider and model" },

  // Editor · General.
  "editor.catalog.label": {
    es: "proveedor (del catálogo: trae la URL y la familia puestas)",
    en: "provider (from the catalogue: it brings the URL and family filled in)",
  },
  "editor.catalog.choose": { es: "— elegir proveedor —", en: "— choose a provider —" },
  "editor.name.label": { es: "nombre (identidad del proveedor)", en: "name (provider identity)" },
  "editor.family.label": { es: "familia de API (protocolo)", en: "API family (protocol)" },
  "editor.family.openai": { es: "openai (compatible)", en: "openai (compatible)" },
  "editor.family.anthropic": { es: "anthropic", en: "anthropic" },
  "editor.model.label": { es: "modelo", en: "model" },
  "editor.model.placeholder": {
    es: "elige de la lista o escríbelo",
    en: "pick it from the list or type it",
  },
  "editor.think.title": { es: "canal Think: {canal}", en: "Think channel: {canal}" },
  "editor.think.none": {
    es: "este modelo no declara canal Think",
    en: "this model does not declare a Think channel",
  },
  "editor.think.inferred": {
    es: " · niveles deducidos del nombre",
    en: " · levels inferred from the name",
  },
  "editor.think.noneAndLevels": {
    es: "este modelo no declara Think ni niveles; el control queda deshabilitado",
    en: "this model declares neither Think nor levels; the control stays disabled",
  },
  "editor.think.hint": {
    es: "la lista se carga desde «Conexión»; elige un modelo para ver sus capacidades.",
    en: "the list is loaded from “Connection”; pick a model to see its capabilities.",
  },
  "editor.effort.aria": {
    es: "esfuerzo de razonamiento del modelo",
    en: "model reasoning effort",
  },
  "editor.effort.title": {
    es: "auto = lo decide el proveedor · off = apaga el razonamiento (si se puede)",
    en: "auto = the provider decides · off = turns reasoning off (if it can)",
  },
  "editor.effort.auto": { es: "auto — lo decide el proveedor", en: "auto — the provider decides" },
  "editor.effort.off": { es: "off — apaga el razonamiento", en: "off — turns reasoning off" },
  "editor.effort.saved": { es: "{nivel} (guardado)", en: "{nivel} (saved)" },
  "editor.effort.none": { es: "sin niveles declarados", en: "no levels declared" },
  "editor.activation": { es: "activación", en: "activation" },
  "editor.activation.saveAndActivate": {
    es: "guardar y dejar activo",
    en: "save and leave it active",
  },
  "editor.activation.saveNote": {
    es: "al guardar, este proveedor pasa a ser el global del runtime.",
    en: "on saving, this provider becomes the runtime's global one.",
  },
  "editor.activation.isActive": { es: "es el proveedor activo", en: "it is the active provider" },
  "editor.activation.rolesNote": {
    es: "los roles pueden fijar otro proveedor por separado.",
    en: "roles can set a different provider separately.",
  },
  "editor.activation.activateOnSave": {
    es: "activar al guardar (pasa a ser el global)",
    en: "activate on save (it becomes the global one)",
  },
  "editor.activation.activateNote": {
    es: "sin marcarlo se guardan los cambios y el proveedor global no cambia.",
    en: "without ticking it the changes are saved and the global provider does not change.",
  },

  // Editor · Conexión.
  "editor.baseUrl": { es: "URL base", en: "Base URL" },
  "editor.baseUrl.needsEdit": {
    es: " (este proveedor pide editarla)",
    en: " (this provider asks you to edit it)",
  },
  "editor.envVar": {
    es: "variable de entorno (recomendado)",
    en: "environment variable (recommended)",
  },
  "editor.commandcode.keyHint": {
    es: "Hermes usa COMMANDCODE_API_KEY en ~/.hermes/.env; Satellite no lee ese archivo. Pega la clave en el campo de clave o configura esa variable para Satellite.",
    en: "Hermes uses COMMANDCODE_API_KEY in ~/.hermes/.env; Satellite does not read that file. Paste the key in the key field or configure that variable for Satellite.",
  },
  "editor.key.new": {
    es: "clave nueva (vacío = mantener la guardada)",
    en: "new key (empty = keep the saved one)",
  },
  "editor.key.or": { es: "o la clave", en: "or the key" },
  "editor.key.placeholder.edit": { es: "no se vuelve a mostrar", en: "it is not shown again" },
  "editor.key.savedNote": {
    es: "sin clave nueva se usan las guardadas del servidor",
    en: "with no new key, the ones saved on the server are used",
  },
  "editor.account": { es: "cuenta (account id)", en: "account (account id)" },
  "editor.account.placeholder": {
    es: "sólo si el listado de modelos es por cuenta (Fireworks, Cloudflare…)",
    en: "only if the model listing is per account (Fireworks, Cloudflare…)",
  },
  "editor.loadModels": { es: "Cargar/refrescar modelos", en: "Load/refresh models" },
  "editor.noDetail": { es: "sin detalle", en: "no detail" },
  "editor.theProvider": { es: "el proveedor", en: "the provider" },
  "editor.catalog.result": {
    es: "{n} modelos de {etiqueta} ({url})",
    en: "{n} models from {etiqueta} ({url})",
  },
  "editor.catalog.authError": {
    es: "{etiqueta} rechazó la credencial (HTTP {codigo}) en {url}.{detalle} Revisa la API key o los permisos de la cuenta. Los modelos ya listados se conservan.",
    en: "{etiqueta} rejected the credential (HTTP {codigo}) at {url}.{detalle} Check the API key or account permissions. The models already listed are kept.",
  },
  "editor.catalog.empty": {
    es: "{etiqueta} respondió sin modelos ({url})",
    en: "{etiqueta} answered with no models ({url})",
  },
  "editor.catalog.error": {
    es: "no pude listar modelos de {etiqueta} en {url}: {error}",
    en: "I could not list models from {etiqueta} at {url}: {error}",
  },

  // Editor · Generación.
  "editor.generation.note": {
    es: "El interruptor Think y el nivel de esfuerzo se ajustan junto al modelo, en «General». Aquí queda el tope de salida.",
    en: "The Think switch and the effort level are set next to the model, in “General”. The output cap is here.",
  },
  "editor.maxTokens.label": {
    es: "tope de tokens de salida (vacío = lo decide el proveedor)",
    en: "output token cap (empty = the provider decides)",
  },
  "editor.maxTokens.placeholder": { es: "p. ej. 4096", en: "e.g. 4096" },
  "editor.maxTokens.note.a": { es: "se envía como ", en: "it is sent as " },
  "editor.maxTokens.note.b": { es: ", o ", en: ", or " },
  "editor.maxTokens.note.c": { es: " en modelos de razonamiento.", en: " on reasoning models." },

  // Editor · Avanzado.
  "editor.advanced.note": {
    es: "Sólo lo que el runtime de verdad persiste y usa para este modelo. El proveedor anuncia sus capacidades de esfuerzo al listar los modelos, y viajan con el modelo.",
    en: "Only what the runtime really persists and uses for this model. The provider announces its effort capabilities when listing the models, and they travel with the model.",
  },
  "editor.advanced.channel": { es: "canal: ", en: "channel: " },
  "editor.advanced.levels": { es: "niveles: {niveles}", en: "levels: {niveles}" },
  "editor.advanced.canDisable": {
    es: "se puede apagar el razonamiento: {valor}",
    en: "reasoning can be turned off: {valor}",
  },
  "editor.yes": { es: "sí", en: "yes" },
  "editor.no": { es: "no", en: "no" },
  "editor.advanced.origin": {
    es: "origen: deducido del nombre del modelo",
    en: "origin: inferred from the model name",
  },
  "editor.advanced.empty.none": {
    es: "Este modelo no declara capacidades de esfuerzo: no se envía ningún parámetro.",
    en: "This model does not declare effort capabilities: no parameter is sent.",
  },
  "editor.advanced.empty.choose": {
    es: "Elige un modelo para ver las capacidades que el runtime enviará.",
    en: "Pick a model to see the capabilities the runtime will send.",
  },
  "editor.advanced.footer": {
    es: "Temperatura, top_p, penalizaciones y timeout no se ofrecen aquí: el runtime todavía no los lee, y no se guarda lo que se ignora.",
    en: "Temperature, top_p, penalties and timeout are not offered here: the runtime does not read them yet, and what is ignored is not saved.",
  },

  // Editor · guardado, errores y pie.
  "editor.saving": { es: "guardando…", en: "saving…" },
  "editor.saveChanges": { es: "Guardar cambios", en: "Save changes" },
  "editor.saveAndActivate": { es: "Guardar y activar", en: "Save and activate" },
  "editor.error.baseUrl": {
    es: "falta la URL base para listar modelos",
    en: "the base URL to list models is missing",
  },
  "editor.error.required": {
    es: "nombre, modelo y URL base son obligatorios",
    en: "name, model and base URL are required",
  },
  "editor.error.maxTokens": {
    es: "el tope de salida debe ser un número mayor o igual que 0",
    en: "the output cap must be a number greater than or equal to 0",
  },
  "editor.saved.updated": {
    es: "modelo «{nombre} · {modelo}» actualizado{extra}",
    en: "model “{nombre} · {modelo}” updated{extra}",
  },
  "editor.saved.updated.activated": { es: " y activado", en: " and activated" },
  "editor.saved.created": {
    es: "modelo «{nombre} · {modelo}» guardado y activo",
    en: "model “{nombre} · {modelo}” saved and active",
  },

  // ----------------------------------------------------- Barra de título (extra)
  "titlebar.bar": { es: "Barra de título", en: "Title bar" },

  // ------------------------------------------------------------------ Comunes
  "common.escClose": { es: "Esc · cerrar", en: "Esc · close" },

  // ----------------------------------------------------------- Asistente inicial
  "onboarding.later": { es: "más tarde", en: "later" },
  "onboarding.title": { es: "Empecemos", en: "Let's start" },
  "onboarding.subtitle": { es: "proyecto y proveedor", en: "project and provider" },
  "onboarding.project.title": { es: "1 · Proyecto", en: "1 · Project" },
  "onboarding.project.open": { es: "Carpeta abierta:", en: "Folder open:" },
  "onboarding.project.none": {
    es: "Aún no has elegido carpeta: sin elegirla, Satellite usaría la suya.",
    en: "You have not chosen a folder yet: without one, Satellite would use its own.",
  },
  "onboarding.project.choose": { es: "Elegir carpeta…", en: "Choose folder…" },
  "onboarding.provider.title": { es: "2 · Proveedor y modelo", en: "2 · Provider and model" },
  "onboarding.provider.note": {
    es: "Del catálogo sale la URL ya puesta: normalmente sólo pegas la clave. La clave no se vuelve a mostrar.",
    en: "The catalogue fills the URL in: usually you only paste the key. The key is not shown again.",
  },
  "onboarding.provider.catalog": { es: "del catálogo", en: "from the catalogue" },
  "onboarding.provider.name": { es: "nombre", en: "name" },
  "onboarding.provider.family": { es: "familia de API", en: "API family" },
  "onboarding.family.openai": { es: "openai (compatible)", en: "openai (compatible)" },
  "onboarding.family.anthropic": { es: "anthropic", en: "anthropic" },
  "onboarding.test": { es: "Probar conexión", en: "Test connection" },
  "onboarding.testing": { es: "probando…", en: "testing…" },
  "onboarding.test.ok": { es: "conexión correcta: {n} modelos en {url}", en: "connection ok: {n} models at {url}" },
  "onboarding.test.authError": {
    es: "{etiqueta} rechazó la credencial (HTTP {codigo}) en {url}. Revisa la API key y vuelve a intentarlo; o deja la variable de entorno con la clave.",
    en: "{etiqueta} rejected the credential (HTTP {codigo}) at {url}. Check the API key and try again; or leave the environment variable with the key.",
  },
  "onboarding.test.empty": { es: "{url} respondió sin modelos", en: "{url} answered with no models" },
  "onboarding.test.error": { es: "no pude conectar con {url}: {error}", en: "I could not connect to {url}: {error}" },
  "onboarding.state.title": { es: "3 · Estado", en: "3 · State" },
  "onboarding.state.ready": {
    es: "Runtime listo: ya puedes escribir un objetivo en la conversación.",
    en: "Runtime ready: you can now write a goal in the conversation.",
  },
  "onboarding.state.providerNoRuntime": {
    es: "Hay proveedor guardado, pero el runtime no arrancó: {error}",
    en: "A provider is saved, but the runtime did not start: {error}",
  },
  "onboarding.state.noProvider": {
    es: "Sin proveedor todavía: el runtime no puede ejecutar tareas.",
    en: "No provider yet: the runtime cannot run tasks.",
  },
  "onboarding.state.recheck": { es: "Volver a comprobar", en: "Check again" },
  "onboarding.state.start": { es: "Empezar", en: "Start" },

  // ------------------------------------------- Edición rápida (píldora del modelo)
  "quick.aria": { es: "modelo activo: {etiqueta}", en: "active model: {etiqueta}" },
  "quick.title": {
    es: "modelo activo: {etiqueta} · clic para cambiar modelo, Think y esfuerzo",
    en: "active model: {etiqueta} · click to change model, Think and effort",
  },
  "quick.menu.aria": {
    es: "modelo, Think y esfuerzo del proveedor activo",
    en: "model, Think and effort of the active provider",
  },
  "quick.provider": { es: "proveedor activo", en: "active provider" },
  "quick.model": { es: "modelo del proveedor", en: "provider model" },
  "quick.current": { es: " (actual)", en: " (current)" },
  "quick.noModels": { es: "— sin modelos —", en: "— no models —" },
  "quick.noList.note": {
    es: ": se conserva el modelo actual (no se inventan modelos)",
    en: ": the current model is kept (models are not made up)",
  },
  "quick.list.auth": {
    es: "el proveedor rechazó la credencial (HTTP {codigo})",
    en: "the provider rejected the credential (HTTP {codigo})",
  },
  "quick.list.error": { es: "no se pudo listar: {error}", en: "it could not be listed: {error}" },
  "quick.list.none": { es: "el proveedor no devolvió modelos", en: "the provider returned no models" },
  "quick.think.disabled": {
    es: "este modelo no declara un canal Think: control deshabilitado",
    en: "this model does not declare a Think channel: control disabled",
  },
  "quick.effort": { es: "esfuerzo", en: "effort" },
  "quick.effort.aria": { es: "esfuerzo del modelo activo", en: "active model effort" },
  "quick.effort.saved": { es: "esfuerzo {valor}", en: "effort {valor}" },
  "quick.think.on": { es: "think activado", en: "think enabled" },
  "quick.think.off": { es: "think desactivado", en: "think disabled" },
  "quick.model.applied": { es: "modelo «{modelo}» aplicado", en: "model “{modelo}” applied" },
  "quick.applying": { es: "aplicando…", en: "applying…" },
  "quick.applyModel": { es: "Aplicar modelo {modelo}", en: "Apply model {modelo}" },

  // -------------------------------------------------------------- Atajos (ayuda)
  "shortcuts.title": { es: "atajos", en: "shortcuts" },
  "shortcuts.palette": { es: "paleta de comandos", en: "command palette" },
  "shortcuts.newChat": { es: "nueva conversación", en: "new conversation" },
  "shortcuts.leftPanel": { es: "panel izquierdo", en: "left panel" },
  "shortcuts.inspector": { es: "inspector", en: "inspector" },
  "shortcuts.settings": { es: "ajustes", en: "settings" },
  "shortcuts.send": { es: "enviar · Shift+Enter salta línea", en: "send · Shift+Enter adds a line" },
  "shortcuts.esc": { es: "cerrar", en: "close" },

  // ------------------------------------------------------- Avisos y diálogos (App)
  "app.blocked.run": {
    es: "sin proyecto elegido: abre una carpeta antes de ejecutar tareas",
    en: "no project chosen: open a folder before running tasks",
  },
  "app.blocked.newSession": {
    es: "sin proyecto elegido: abre una carpeta antes de crear conversaciones",
    en: "no project chosen: open a folder before creating conversations",
  },
  "app.busy.enqueue": {
    es: "turno en curso: usa «encolar» para la siguiente instrucción",
    en: "turn in progress: use “queue” for the next instruction",
  },
  "app.context.summary": { es: "contexto: {resumen}", en: "context: {resumen}" },
  "app.cancel.cooperative": { es: "cooperativa", en: "cooperative" },
  "app.cancel.forced": { es: "corte forzado", en: "forced stop" },
  "app.cancel.done": { es: "turno cancelado ({via}): {nota}", en: "turn cancelled ({via}): {nota}" },
  "app.cancel.defaultNote": {
    es: "lo hecho queda en el historial",
    en: "what was done stays in the history",
  },
  "app.worktree.created": { es: "worktree «{nombre}» creado", en: "worktree “{nombre}” created" },
  "app.worktree.changedNoRuntime": {
    es: "worktree cambiado, pero el runtime no se rearmó: {error}",
    en: "worktree changed, but the runtime could not be re-armed: {error}",
  },
  "app.worktree.runsIn": { es: "el agente ejecutará en {destino}", en: "the agent will run in {destino}" },
  "app.worktree.runsProject": {
    es: "el agente ejecutará en el proyecto",
    en: "the agent will run in the project",
  },
  "app.worktree.removeTitle": { es: "Quitar el worktree", en: "Remove the worktree" },
  "app.worktree.removeForceTitle": {
    es: "Quitar el worktree (forzando)",
    en: "Remove the worktree (forcing it)",
  },
  "app.worktree.removeDetail": {
    es: "Se elimina el worktree {ruta}. Si tiene cambios sin confirmar, se rechazará.",
    en: "The worktree {ruta} is deleted. If it has uncommitted changes, it will be refused.",
  },
  "app.worktree.removeForceDetail": {
    es: "Se descarta el contenido sin confirmar de {ruta} y se elimina el worktree.",
    en: "The uncommitted content of {ruta} is discarded and the worktree is deleted.",
  },
  "app.worktree.removed": { es: "worktree quitado: {ruta}", en: "worktree removed: {ruta}" },
  "app.queue.enqueued": {
    es: "instrucción encolada: se ejecutará al terminar el turno en curso",
    en: "instruction queued: it will run when the current turn finishes",
  },
  "app.queue.empty": { es: "(cola vacía)", en: "(empty queue)" },
  "app.queue.title": { es: "cola de instrucciones", en: "instruction queue" },
  "app.queue.note": {
    es: "Se encola y ejecuta desde el compositor (botón «encolar»).",
    en: "It is queued and run from the composer (“queue” button).",
  },
  "app.restore.detailTitle": {
    es: "Se descartará lo hecho en la tarea {id}",
    en: "What the task {id} did will be discarded",
  },
  "app.restore.title": { es: "Restaurar los cambios de la tarea", en: "Restore the task changes" },
  "app.restore.confirmDetail": {
    es: "{n} archivo(s) volverán a su estado anterior.",
    en: "{n} file(s) will go back to their previous state.",
  },
  "app.restore.done": {
    es: "restaurados {restaurados}, eliminados {eliminados}",
    en: "restored {restaurados}, deleted {eliminados}",
  },
  "app.noDiff": { es: "sin diff", en: "no diff" },
  "app.job.stopped": { es: "trabajo {id} detenido", en: "job {id} stopped" },
  "app.job.outputTitle": { es: "salida {id}", en: "output {id}" },
  "app.job.noOutput": { es: "(sin salida)", en: "(no output)" },
  "app.job.status": {
    es: "trabajo {id}: {estado} ({codigo})",
    en: "job {id}: {estado} ({codigo})",
  },
  "app.session.noActiveRename": {
    es: "no hay conversación activa que renombrar",
    en: "there is no active conversation to rename",
  },
  "app.session.noActive": { es: "no hay conversación activa", en: "there is no active conversation" },
  "app.session.unknownAction": {
    es: "acción desconocida: usa new, use, name, pin, unpin, archive o unarchive",
    en: "unknown action: use new, use, name, pin, unpin, archive or unarchive",
  },
  "app.history.empty": { es: "(conversación vacía)", en: "(empty conversation)" },
  "app.history.title": { es: "conversación actual", en: "current conversation" },
  "app.context.title": { es: "contexto", en: "context" },
  "app.context.meta": {
    es: "real={real} estimado={estimado} ventana={ventana} ({fuente})",
    en: "real={real} estimated={estimado} window={ventana} ({fuente})",
  },
  "app.provider.needBaseUrl": {
    es: "indica base_url o activa un proveedor con URL",
    en: "give a base_url or activate a provider with a URL",
  },
  "app.provider.deleteTitle": { es: "Eliminar el proveedor {id}", en: "Delete the provider {id}" },
  "app.provider.deleteDetail": {
    es: "Se borra de la configuración guardada. Si era el activo, habrá que elegir otro.",
    en: "It is deleted from the saved configuration. If it was the active one, another must be chosen.",
  },
  "app.provider.savedActive": {
    es: "proveedor «{nombre}» guardado y activo",
    en: "provider “{nombre}” saved and active",
  },
  "app.mode.unknown": {
    es: "modo desconocido «{modo}»: usa ask, auto o unrestricted",
    en: "unknown mode “{modo}”: use ask, auto or unrestricted",
  },
  "app.snapshot.restoreTitle": {
    es: "Restaurar el snapshot {nombre}",
    en: "Restore the snapshot {nombre}",
  },
  "app.snapshot.restoreDetail": {
    es: "El estado actual se descarta: agentes, skills y configuración vuelven a los del snapshot.",
    en: "The current state is discarded: agents, skills and configuration go back to the snapshot's.",
  },
  "app.config.title": { es: "configuración (sin secretos)", en: "configuration (no secrets)" },
  "app.commands.title": { es: "catálogo de comandos", en: "command catalogue" },
  "app.commands.gaps": { es: "huecos de paridad:", en: "parity gaps:" },
  "app.commands.none": { es: "(ninguno)", en: "(none)" },
  "app.tui.title": { es: "interfaz de terminal (TUI)", en: "terminal interface (TUI)" },
  "app.tui.line1": {
    es: "La TUI es la interfaz de terminal: esta app gráfica no la abre por ti.",
    en: "The TUI is the terminal interface: this graphical app does not open it for you.",
  },
  "app.tui.line2": {
    es: "Ejecuta esto en una terminal, con el Python del proyecto:",
    en: "Run this in a terminal, with the project's Python:",
  },
  "app.tui.copied": { es: "Comando copiado al portapapeles.", en: "Command copied to the clipboard." },
  "app.tui.copyManual": {
    es: "Selecciona el comando de arriba y cópialo en una terminal.",
    en: "Select the command above and copy it in a terminal.",
  },
  "app.help.title": { es: "Comandos disponibles (Ctrl+K):", en: "Available commands (Ctrl+K):" },
  "app.help.freeText": {
    es: "  (texto libre)  Envía un mensaje al agente (conversación)",
    en: "  (free text)  Sends a message to the agent (conversation)",
  },
  "app.help.detailTitle": { es: "ayuda", en: "help" },
  "app.command.unknown": {
    es: "la app todavía no sabe ejecutar «/{clave}»",
    en: "the app does not know how to run “/{clave}” yet",
  },
  "app.backend.restartFailed": {
    es: "no se pudo reiniciar el backend",
    en: "the backend could not be restarted",
  },
  "app.git.aria": { es: "estado de git: abrir la pestaña Git", en: "git status: open the Git tab" },
  "app.git.title.worktree": {
    es: "rama {rama} · ejecutando en el worktree {ruta}",
    en: "branch {rama} · running in the worktree {ruta}",
  },
  "app.git.title.project": {
    es: "rama {rama} · ejecutando en el proyecto",
    en: "branch {rama} · running in the project",
  },
  "app.skill.updated": { es: "skill {id} actualizada", en: "skill {id} updated" },
  "app.skill.deleteTitle": { es: "Borrar la skill {id}", en: "Delete the skill {id}" },
  "app.skill.deleteDetail": {
    es: "Se elimina del catálogo de skills del proyecto. No se puede deshacer.",
    en: "It is deleted from the project's skills catalogue. It cannot be undone.",
  },
  "app.events.title": { es: "eventos de {id}", en: "events for {id}" },
  "app.events.empty": { es: "(sin eventos)", en: "(no events)" },
  "app.trace.title": { es: "traza de {id}", en: "trace for {id}" },
  "app.trace.none": {
    es: "Sin traza para {id} (la tarea no existe o es de una versión anterior).",
    en: "No trace for {id} (the task does not exist or is from an earlier version).",
  },
  "app.usage.sessionUse": { es: "uso: session use <id|nombre>", en: "usage: session use <id|name>" },
  "app.usage.sessionName": { es: "uso: session name <nombre>", en: "usage: session name <name>" },
  "app.usage.sessionPin": {
    es: "uso: session pin|unpin <id|nombre>",
    en: "usage: session pin|unpin <id|name>",
  },
  "app.usage.sessionArchive": {
    es: "uso: session archive|unarchive <id|nombre>",
    en: "usage: session archive|unarchive <id|name>",
  },

  // ------------------------------------------- Títulos de los volcados (paleta)
  "app.detail.sessions": { es: "conversaciones", en: "conversations" },
  "app.detail.compact": { es: "compactar contexto", en: "compact context" },
  "app.detail.agentCatalog": { es: "catálogo de agentes", en: "agents catalogue" },
  "app.detail.createAgent": { es: "crear agente", en: "create agent" },
  "app.detail.skillCatalog": { es: "catálogo de skills", en: "skills catalogue" },
  "app.detail.searchSkills": { es: "buscar skills", en: "search skills" },
  "app.detail.deleteSkill": { es: "borrar skill", en: "delete skill" },
  "app.detail.curate": { es: "curador de skills", en: "skills curator" },
  "app.detail.runtimeTools": { es: "herramientas del runtime", en: "runtime tools" },
  "app.detail.jobs": { es: "trabajos", en: "jobs" },
  "app.detail.tasks": { es: "tareas", en: "tasks" },
  "app.detail.savedProviders": { es: "proveedores guardados", en: "saved providers" },
  "app.detail.providerCatalog": { es: "catálogo de proveedores", en: "providers catalogue" },
  "app.detail.providerModels": { es: "modelos del proveedor", en: "provider models" },
  "app.detail.deleteProvider": { es: "eliminar proveedor", en: "delete provider" },
  "app.detail.doctor": { es: "doctor", en: "doctor" },
  "app.detail.stats": { es: "stats", en: "stats" },
  "app.detail.setup": { es: "setup · sandbox", en: "setup · sandbox" },
  "app.detail.init": { es: "init", en: "init" },
  "app.detail.reset": { es: "reset", en: "reset" },
  "app.detail.snapshots": { es: "snapshots", en: "snapshots" },
  "app.detail.restoreSnapshot": { es: "restaurar snapshot", en: "restore snapshot" },

  // ------------------------------------------------- Etiquetas de eventos (api.ts)
  "event.task_started": { es: "tarea iniciada", en: "task started" },
  "event.graph_built": { es: "plan construido", en: "plan built" },
  "event.node_started": { es: "nodo en marcha", en: "node running" },
  "event.node_finished": { es: "nodo terminado", en: "node finished" },
  "event.tool_called": { es: "herramienta", en: "tool" },
  "event.validation_failed": { es: "validación fallida", en: "validation failed" },
  "event.retry": { es: "reintento", en: "retry" },
  "event.node_stuck": { es: "nodo atascado", en: "node stuck" },
  "event.task_finished": { es: "tarea terminada", en: "task finished" },
  "event.plan_failed": { es: "plan fallido", en: "plan failed" },
  "event.agent_proposed": { es: "agente propuesto", en: "agent proposed" },
  "event.agent_approved": { es: "agente aprobado", en: "agent approved" },
  "event.agent_rejected": { es: "agente rechazado", en: "agent rejected" },
  "event.agent_created": { es: "agente creado", en: "agent created" },
  "event.agent_extended": { es: "agente ampliado", en: "agent extended" },
  "event.skill_proposed": { es: "skill propuesta", en: "skill proposed" },
  "event.skill_approved": { es: "skill aprobada", en: "skill approved" },
  "event.skill_searched": { es: "skill buscada (considerada)", en: "skill searched (considered)" },
  "event.skill_executed": { es: "skill ejecutada", en: "skill executed" },
  "event.skill_failed": { es: "skill fallida", en: "skill failed" },
  "event.skill_quarantined": { es: "skill en cuarentena", en: "skill quarantined" },
  "event.delegation_started": { es: "delegación iniciada", en: "delegation started" },
  "event.delegation_completed": { es: "delegación terminada", en: "delegation finished" },
  "event.policy_violation": { es: "permiso denegado", en: "permission denied" },
  "event.safe_mode_entered": { es: "modo seguro", en: "safe mode" },
  "event.unknown": { es: "evento", en: "event" },

  // ------------------------------------------------- Catálogo de comandos (backend)
  // El catálogo sigue viviendo en `satellite/commands.py`: aquí sólo se traduce. Las claves `es`
  // son **exactamente** las cadenas que sirve el backend; `en` es su traducción. No se traducen
  // los ids, el `usage` ni los valores técnicos (flags, familias, `ask|auto|unrestricted`…).
  // Categorías compartidas: el backend repite la misma cadena en cada comando del grupo.
  "cmd.category.project": { es: "Proyecto", en: "Project" },
  "cmd.category.conversation": { es: "Conversación", en: "Conversation" },
  "cmd.category.agents": { es: "Agentes y skills", en: "Agents and skills" },
  "cmd.category.tasks": { es: "Tareas y trabajos", en: "Tasks and jobs" },
  "cmd.category.providers": { es: "Proveedores y roles", en: "Providers and roles" },
  "cmd.category.security": { es: "Seguridad y diagnóstico", en: "Security and diagnostics" },
  "cmd.category.app": { es: "Aplicación", en: "Application" },

  // Resúmenes.
  "cmd.project.summary": { es: "Abrir otra carpeta de proyecto", en: "Open another project folder" },
  "cmd.sessions.summary": { es: "Ver las conversaciones guardadas", en: "See the saved conversations" },
  "cmd.new.summary": { es: "Empezar una conversación nueva", en: "Start a new conversation" },
  "cmd.session.summary": { es: "Crea, reanuda, renombra, fija o archiva una conversación", en: "Create, resume, rename, pin or archive a conversation" },
  "cmd.git.summary": { es: "Estado real de git (rama, cambios, conflictos)", en: "Real git status (branch, changes, conflicts)" },
  "cmd.worktrees.summary": { es: "Worktrees del repo y contexto de ejecución activo", en: "Repo worktrees and the active execution context" },
  "cmd.comments.summary": { es: "Comentarios inline del diff: listar, crear, resolver y borrar", en: "Inline diff comments: list, create, resolve and delete" },
  "cmd.panel-left.summary": { es: "Mostrar u ocultar el panel izquierdo", en: "Show or hide the left panel" },
  "cmd.panel-right.summary": { es: "Mostrar u ocultar el inspector", en: "Show or hide the inspector" },
  "cmd.run.summary": { es: "Envía un mensaje al agente (igual que texto libre)", en: "Send a message to the agent (same as free text)" },
  "cmd.plan.summary": { es: "Modo plan: investiga sin cambiar nada y propone enfoques", en: "Plan mode: investigates without changing anything and proposes approaches" },
  "cmd.context.summary": { es: "Uso de contexto del modelo", en: "Model context usage" },
  "cmd.compact.summary": { es: "Compactar el contexto de la conversación", en: "Compact the conversation context" },
  "cmd.cancel.summary": { es: "Cancelar el turno en curso", en: "Cancel the current turn" },
  "cmd.history.summary": { es: "Muestra la conversación actual", en: "Show the current conversation" },
  "cmd.queue.summary": { es: "Cola de instrucciones: listar, encolar, editar y ejecutar", en: "Instruction queue: list, enqueue, edit and run" },
  "cmd.agents.summary": { es: "Ver el catálogo de agentes", en: "See the agents catalogue" },
  "cmd.create.summary": { es: "Crear un agente para una necesidad", en: "Create an agent for a need" },
  "cmd.skills.summary": { es: "Ver el catálogo de skills", en: "See the skills catalogue" },
  "cmd.skill-search.summary": { es: "Buscar en las skills", en: "Search the skills" },
  "cmd.skill-edit.summary": { es: "Editar una skill", en: "Edit a skill" },
  "cmd.skill-delete.summary": { es: "Eliminar una skill", en: "Delete a skill" },
  "cmd.curate.summary": { es: "Ejecutar el curador de skills", en: "Run the skills curator" },
  "cmd.tools.summary": { es: "Ver las herramientas del runtime", en: "See the runtime tools" },
  "cmd.tasks.summary": { es: "Listar las tareas persistidas", en: "List the persisted tasks" },
  "cmd.status.summary": { es: "Eventos de una tarea", en: "Events of a task" },
  "cmd.inspect.summary": { es: "Qué hizo una tarea: tools y delegaciones", en: "What a task did: tools and delegations" },
  "cmd.changes.summary": { es: "Archivos y diff que cambió una tarea", en: "Files and diff a task changed" },
  "cmd.undo-task.summary": { es: "Restaurar los cambios de esa tarea", en: "Restore that task's changes" },
  "cmd.jobs.summary": { es: "Comandos largos en segundo plano", en: "Long commands in the background" },
  "cmd.job-output.summary": { es: "Ver la salida de un trabajo", en: "See a job's output" },
  "cmd.job-wait.summary": { es: "Esperar a que termine un trabajo", en: "Wait for a job to finish" },
  "cmd.job-stop.summary": { es: "Detener un trabajo", en: "Stop a job" },
  "cmd.provider.summary": { es: "Configurar el proveedor (catálogo + modelos)", en: "Configure the provider (catalogue + models)" },
  "cmd.providers.summary": { es: "Ver los proveedores guardados", en: "See the saved providers" },
  "cmd.provider-catalog.summary": { es: "Ver el catálogo de proveedores conocidos", en: "See the catalogue of known providers" },
  "cmd.provider-models.summary": { es: "Listar los modelos de una URL", en: "List the models of a URL" },
  "cmd.provider-add.summary": { es: "Guardar un proveedor sin asistente", en: "Save a provider without the wizard" },
  "cmd.provider-use.summary": { es: "Dejar activo un proveedor guardado", en: "Make a saved provider active" },
  "cmd.provider-remove.summary": { es: "Eliminar un proveedor guardado", en: "Delete a saved provider" },
  "cmd.roles.summary": { es: "Roles: orchestrator, context, agents, creator, subagents", en: "Roles: orchestrator, context, agents, creator, subagents" },
  "cmd.policy.summary": { es: "Política de seguridad y modo seguro", en: "Security policy and safe mode" },
  "cmd.mode.summary": { es: "Cambiar la política de aprobación", en: "Change the approval policy" },
  "cmd.safe-mode.summary": { es: "Arranque sólo con agentes base", en: "Start-up with base agents only" },
  "cmd.doctor.summary": { es: "Diagnóstico de integridad", en: "Integrity diagnostics" },
  "cmd.stats.summary": { es: "Métricas de skills y caché", en: "Skills and cache metrics" },
  "cmd.setup.summary": { es: "Detectar Docker (sandbox N1)", en: "Detect Docker (sandbox N1)" },
  "cmd.init.summary": { es: "Inicializar o reparar el proyecto", en: "Initialise or repair the project" },
  "cmd.reset.summary": { es: "Limpia agentes creados (y skills con --factory)", en: "Clears created agents (and skills with --factory)" },
  "cmd.snapshots.summary": { es: "Listar snapshots", en: "List snapshots" },
  "cmd.restore.summary": { es: "Restaurar desde un snapshot", en: "Restore from a snapshot" },
  "cmd.config.summary": { es: "Muestra la configuración (sin secretos)", en: "Show the configuration (no secrets)" },
  "cmd.commands.summary": { es: "Ver el catálogo de comandos y sus huecos", en: "See the command catalogue and its gaps" },
  "cmd.tui.summary": { es: "Abrir la interfaz de terminal (TUI)", en: "Open the terminal interface (TUI)" },
  "cmd.shortcuts.summary": { es: "Atajos de teclado", en: "Keyboard shortcuts" },
  "cmd.onboarding.summary": { es: "Asistente inicial: proyecto y proveedor", en: "Initial wizard: project and provider" },
  "cmd.clear.summary": { es: "Limpiar la actividad en pantalla", en: "Clear the on-screen activity" },
  "cmd.help.summary": { es: "Muestra la ayuda", en: "Show the help" },
  "cmd.quit.summary": { es: "Salir de la interfaz", en: "Quit the interface" },

  // Etiquetas de los campos (ArgSpec). Sólo se añade `placeholder` cuando el backend trae uno.
  "cmd.session.arg.accion.label": { es: "new | use | name | pin | unpin | archive | unarchive", en: "new | use | name | pin | unpin | archive | unarchive" },
  "cmd.session.arg.valor.label": { es: "Id o nombre (o el nombre nuevo)", en: "Id or name (or the new name)" },
  "cmd.run.arg.mensaje.label": { es: "Mensaje para el agente", en: "Message for the agent" },
  "cmd.plan.arg.tarea.label": { es: "Tarea ambigua a planificar", en: "Ambiguous task to plan" },
  "cmd.create.arg.necesidad.label": { es: "¿Qué capacidad hace falta?", en: "What capability is needed?" },
  "cmd.skill-search.arg.texto.label": { es: "Texto a buscar", en: "Text to search for" },
  "cmd.skill-edit.arg.id.label": { es: "Id de la skill", en: "Skill id" },
  "cmd.skill-edit.arg.descripcion.label": { es: "Nueva descripción", en: "New description" },
  "cmd.skill-edit.arg.tags.label": { es: "Tags separados por coma", en: "Comma-separated tags" },
  "cmd.skill-delete.arg.id.label": { es: "Id de la skill", en: "Skill id" },
  "cmd.curate.arg.peticion.label": { es: "Petición (opcional)", en: "Request (optional)" },
  "cmd.status.arg.task_id.label": { es: "Id de la tarea", en: "Task id" },
  "cmd.inspect.arg.task_id.label": { es: "Id de la tarea", en: "Task id" },
  "cmd.undo-task.arg.task_id.label": { es: "Id de la tarea", en: "Task id" },
  "cmd.job-output.arg.id.label": { es: "Id del trabajo", en: "Job id" },
  "cmd.job-wait.arg.id.label": { es: "Id del trabajo", en: "Job id" },
  "cmd.job-wait.arg.segundos.label": { es: "Segundos de espera", en: "Seconds to wait" },
  "cmd.job-stop.arg.id.label": { es: "Id del trabajo", en: "Job id" },
  "cmd.provider-models.arg.base_url.label": { es: "URL base (vacío = proveedor activo)", en: "Base URL (empty = active provider)" },
  "cmd.provider-models.arg.family.label": { es: "openai | anthropic", en: "openai | anthropic" },
  "cmd.provider-models.arg.api_key.label": { es: "Clave (opcional)", en: "Key (optional)" },
  "cmd.provider-add.arg.name.label": { es: "Familia: openai | anthropic", en: "Family: openai | anthropic" },
  "cmd.provider-add.arg.model.label": { es: "Id del modelo", en: "Model id" },
  "cmd.provider-add.arg.base_url.label": { es: "URL base", en: "Base URL" },
  "cmd.provider-add.arg.api_key.label": { es: "Clave (opcional)", en: "Key (optional)" },
  "cmd.provider-add.arg.api_key_env.label": { es: "Variable de entorno con la clave (opcional)", en: "Environment variable with the key (optional)" },
  "cmd.provider-use.arg.id.label": { es: "Id del proveedor", en: "Provider id" },
  "cmd.provider-remove.arg.id.label": { es: "Id del proveedor", en: "Provider id" },
  "cmd.mode.arg.modo.label": { es: "ask | auto | unrestricted", en: "ask | auto | unrestricted" },
  "cmd.safe-mode.arg.valor.label": { es: "on | off", en: "on | off" },
  "cmd.restore.arg.nombre.label": { es: "Nombre del snapshot", en: "Snapshot name" },
} satisfies Record<string, Par>;

export type Clave = keyof typeof TEXTOS;

/** Códigos de error del backend con traducción conocida; el resto conserva su mensaje. */
const ERRORES_CONOCIDOS: Record<string, Clave> = {
  backend_down: "error.backend_down",
  unauthorized: "error.unauthorized",
  forbidden_path: "error.forbidden_path",
  project_not_found: "error.project_not_found",
  provider_not_found: "error.provider_not_found",
  invalid_provider: "error.invalid_provider",
  invalid_effort: "error.invalid_effort",
  invalid_think: "error.invalid_think",
  runtime_unavailable: "error.runtime_unavailable",
  session_not_found: "error.session_not_found",
  task_not_found: "error.task_not_found",
  invalid_request: "error.invalid_request",
  invalid_decision: "error.invalid_decision",
};

function formatear(plantilla: string, params?: Record<string, string | number>): string {
  if (!params) return plantilla;
  return plantilla.replace(/\{(\w+)\}/g, (todo, nombre) =>
    nombre in params ? String(params[nombre]) : todo,
  );
}

export function traducir(
  idioma: IdiomaId,
  clave: Clave,
  params?: Record<string, string | number>,
): string {
  return formatear(TEXTOS[clave][idioma], params);
}

interface I18n {
  idioma: IdiomaId;
  setIdioma: (idioma: IdiomaId) => void;
  /** Sincroniza el estado con la preferencia persistida sin reescribirla. */
  sincronizarIdioma: (idioma: IdiomaId) => void;
  t: (clave: Clave, params?: Record<string, string | number>) => string;
  /** Mensaje de error: traduce códigos conocidos; deja intacto lo técnico desconocido. */
  error: (exc: unknown) => string;
}

const Contexto = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [idioma, setEstado] = useState<IdiomaId>(() => {
    const inicial = window.satellite?.theme?.initialLanguage;
    return isIdioma(inicial) ? inicial : DEFAULT_IDIOMA;
  });

  const setIdioma = useCallback((siguiente: IdiomaId) => {
    setEstado(siguiente);
    void bridge.guardarIdioma(siguiente).catch(() => {});
  }, []);

  const sincronizarIdioma = useCallback((siguiente: IdiomaId) => {
    setEstado((actual) => (actual === siguiente ? actual : siguiente));
  }, []);

  const valor = useMemo<I18n>(() => {
    const t = (clave: Clave, params?: Record<string, string | number>) =>
      traducir(idioma, clave, params);
    const error = (exc: unknown) => {
      if (exc instanceof ApiError) {
        const clave = ERRORES_CONOCIDOS[exc.code];
        if (clave) return t(clave);
      }
      return mensajeDe(exc);
    };
    return { idioma, setIdioma, sincronizarIdioma, t, error };
  }, [idioma, setIdioma, sincronizarIdioma]);

  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export function useI18n(): I18n {
  const valor = useContext(Contexto);
  if (!valor) throw new Error("falta I18nProvider");
  return valor;
}

/** Atajo para componentes que sólo necesitan traducir. */
export function useT() {
  return useI18n().t;
}
