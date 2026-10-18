/**
 * Traducción del catálogo de comandos (el que sirve el backend en `GET /commands`).
 *
 * `satellite/commands.py` sigue siendo la única definición y la fuente en español: aquí sólo se
 * mapea cada id estable a las claves tipadas de `TEXTOS` (ver `i18n.tsx`). No se traduce lo que no
 * es texto visible —el `id`, el `usage` ni la sintaxis de flags y valores— y si un comando no tiene
 * entrada en `COMANDOS_TEXTO` se devuelve **tal cual lo sirvió el backend**, sin inventar nada.
 */

import type { CommandSpecView } from "./api";
import type { Clave, useT } from "./i18n";

/** Texto traducible de un comando del catálogo: resumen, categoría y etiquetas de sus campos. */
export interface TextoComando {
  summary: Clave;
  category: Clave;
  args?: Record<string, { label: Clave; placeholder?: Clave }>;
}

/**
 * Mapa id del catálogo → claves de `TEXTOS`. Las categorías son claves compartidas
 * (`cmd.category.<slug>`) porque el backend las repite en todos los comandos del grupo.
 * Un `args[key].placeholder` sólo se declara cuando el backend trae uno no vacío.
 */
export const COMANDOS_TEXTO: Record<string, TextoComando> = {
  // ---------------------------------------------------------------- Proyecto
  "project": {
    summary: "cmd.project.summary",
    category: "cmd.category.project",
  },
  "sessions": {
    summary: "cmd.sessions.summary",
    category: "cmd.category.project",
  },
  "new": {
    summary: "cmd.new.summary",
    category: "cmd.category.project",
  },
  "session": {
    summary: "cmd.session.summary",
    category: "cmd.category.project",
    args: {
      "accion": { label: "cmd.session.arg.accion.label" },
      "valor": { label: "cmd.session.arg.valor.label" },
    },
  },
  "git": {
    summary: "cmd.git.summary",
    category: "cmd.category.project",
  },
  "worktrees": {
    summary: "cmd.worktrees.summary",
    category: "cmd.category.project",
  },
  "comments": {
    summary: "cmd.comments.summary",
    category: "cmd.category.project",
  },
  "panel-left": {
    summary: "cmd.panel-left.summary",
    category: "cmd.category.project",
  },
  "panel-right": {
    summary: "cmd.panel-right.summary",
    category: "cmd.category.project",
  },

  // ----------------------------------------------------------- Conversación
  "run": {
    summary: "cmd.run.summary",
    category: "cmd.category.conversation",
    args: {
      "mensaje": { label: "cmd.run.arg.mensaje.label" },
    },
  },
  "plan": {
    summary: "cmd.plan.summary",
    category: "cmd.category.conversation",
    args: {
      "tarea": { label: "cmd.plan.arg.tarea.label" },
    },
  },
  "context": {
    summary: "cmd.context.summary",
    category: "cmd.category.conversation",
  },
  "compact": {
    summary: "cmd.compact.summary",
    category: "cmd.category.conversation",
  },
  "cancel": {
    summary: "cmd.cancel.summary",
    category: "cmd.category.conversation",
  },
  "history": {
    summary: "cmd.history.summary",
    category: "cmd.category.conversation",
  },
  "queue": {
    summary: "cmd.queue.summary",
    category: "cmd.category.conversation",
  },

  // ------------------------------------------------------ Agentes y skills
  "agents": {
    summary: "cmd.agents.summary",
    category: "cmd.category.agents",
  },
  "create": {
    summary: "cmd.create.summary",
    category: "cmd.category.agents",
    args: {
      "necesidad": { label: "cmd.create.arg.necesidad.label" },
    },
  },
  "skills": {
    summary: "cmd.skills.summary",
    category: "cmd.category.agents",
  },
  "skill-search": {
    summary: "cmd.skill-search.summary",
    category: "cmd.category.agents",
    args: {
      "texto": { label: "cmd.skill-search.arg.texto.label" },
    },
  },
  "skill-edit": {
    summary: "cmd.skill-edit.summary",
    category: "cmd.category.agents",
    args: {
      "id": { label: "cmd.skill-edit.arg.id.label" },
      "descripcion": { label: "cmd.skill-edit.arg.descripcion.label" },
      "tags": { label: "cmd.skill-edit.arg.tags.label" },
    },
  },
  "skill-delete": {
    summary: "cmd.skill-delete.summary",
    category: "cmd.category.agents",
    args: {
      "id": { label: "cmd.skill-delete.arg.id.label" },
    },
  },
  "curate": {
    summary: "cmd.curate.summary",
    category: "cmd.category.agents",
    args: {
      "peticion": { label: "cmd.curate.arg.peticion.label" },
    },
  },
  "tools": {
    summary: "cmd.tools.summary",
    category: "cmd.category.agents",
  },

  // ------------------------------------------------------ Tareas y trabajos
  "tasks": {
    summary: "cmd.tasks.summary",
    category: "cmd.category.tasks",
  },
  "status": {
    summary: "cmd.status.summary",
    category: "cmd.category.tasks",
    args: {
      "task_id": { label: "cmd.status.arg.task_id.label" },
    },
  },
  "inspect": {
    summary: "cmd.inspect.summary",
    category: "cmd.category.tasks",
    args: {
      "task_id": { label: "cmd.inspect.arg.task_id.label" },
    },
  },
  "changes": {
    summary: "cmd.changes.summary",
    category: "cmd.category.tasks",
  },
  "undo-task": {
    summary: "cmd.undo-task.summary",
    category: "cmd.category.tasks",
    args: {
      "task_id": { label: "cmd.undo-task.arg.task_id.label" },
    },
  },
  "jobs": {
    summary: "cmd.jobs.summary",
    category: "cmd.category.tasks",
  },
  "job-output": {
    summary: "cmd.job-output.summary",
    category: "cmd.category.tasks",
    args: {
      "id": { label: "cmd.job-output.arg.id.label" },
    },
  },
  "job-wait": {
    summary: "cmd.job-wait.summary",
    category: "cmd.category.tasks",
    args: {
      "id": { label: "cmd.job-wait.arg.id.label" },
      "segundos": { label: "cmd.job-wait.arg.segundos.label" },
    },
  },
  "job-stop": {
    summary: "cmd.job-stop.summary",
    category: "cmd.category.tasks",
    args: {
      "id": { label: "cmd.job-stop.arg.id.label" },
    },
  },

  // --------------------------------------------------- Proveedores y roles
  "provider": {
    summary: "cmd.provider.summary",
    category: "cmd.category.providers",
  },
  "providers": {
    summary: "cmd.providers.summary",
    category: "cmd.category.providers",
  },
  "provider-catalog": {
    summary: "cmd.provider-catalog.summary",
    category: "cmd.category.providers",
  },
  "provider-models": {
    summary: "cmd.provider-models.summary",
    category: "cmd.category.providers",
    args: {
      "base_url": { label: "cmd.provider-models.arg.base_url.label" },
      "family": { label: "cmd.provider-models.arg.family.label" },
      "api_key": { label: "cmd.provider-models.arg.api_key.label" },
    },
  },
  "provider-add": {
    summary: "cmd.provider-add.summary",
    category: "cmd.category.providers",
    args: {
      "name": { label: "cmd.provider-add.arg.name.label" },
      "model": { label: "cmd.provider-add.arg.model.label" },
      "base_url": { label: "cmd.provider-add.arg.base_url.label" },
      "api_key": { label: "cmd.provider-add.arg.api_key.label" },
      "api_key_env": { label: "cmd.provider-add.arg.api_key_env.label" },
    },
  },
  "provider-use": {
    summary: "cmd.provider-use.summary",
    category: "cmd.category.providers",
    args: {
      "id": { label: "cmd.provider-use.arg.id.label" },
    },
  },
  "provider-remove": {
    summary: "cmd.provider-remove.summary",
    category: "cmd.category.providers",
    args: {
      "id": { label: "cmd.provider-remove.arg.id.label" },
    },
  },
  "roles": {
    summary: "cmd.roles.summary",
    category: "cmd.category.providers",
  },

  // ---------------------------------------------- Seguridad y diagnóstico
  "policy": {
    summary: "cmd.policy.summary",
    category: "cmd.category.security",
  },
  "mode": {
    summary: "cmd.mode.summary",
    category: "cmd.category.security",
    args: {
      "modo": { label: "cmd.mode.arg.modo.label" },
    },
  },
  "safe-mode": {
    summary: "cmd.safe-mode.summary",
    category: "cmd.category.security",
    args: {
      "valor": { label: "cmd.safe-mode.arg.valor.label" },
    },
  },
  "doctor": {
    summary: "cmd.doctor.summary",
    category: "cmd.category.security",
  },
  "stats": {
    summary: "cmd.stats.summary",
    category: "cmd.category.security",
  },
  "setup": {
    summary: "cmd.setup.summary",
    category: "cmd.category.security",
  },
  "init": {
    summary: "cmd.init.summary",
    category: "cmd.category.security",
  },
  "reset": {
    summary: "cmd.reset.summary",
    category: "cmd.category.security",
  },
  "snapshots": {
    summary: "cmd.snapshots.summary",
    category: "cmd.category.security",
  },
  "restore": {
    summary: "cmd.restore.summary",
    category: "cmd.category.security",
    args: {
      "nombre": { label: "cmd.restore.arg.nombre.label" },
    },
  },

  // --------------------------------------------------------------- Aplicación
  "config": {
    summary: "cmd.config.summary",
    category: "cmd.category.app",
  },
  "commands": {
    summary: "cmd.commands.summary",
    category: "cmd.category.app",
  },
  "tui": {
    summary: "cmd.tui.summary",
    category: "cmd.category.app",
  },
  "shortcuts": {
    summary: "cmd.shortcuts.summary",
    category: "cmd.category.app",
  },
  "onboarding": {
    summary: "cmd.onboarding.summary",
    category: "cmd.category.app",
  },
  "clear": {
    summary: "cmd.clear.summary",
    category: "cmd.category.app",
  },
  "help": {
    summary: "cmd.help.summary",
    category: "cmd.category.app",
  },
  "quit": {
    summary: "cmd.quit.summary",
    category: "cmd.category.app",
  },
};

/**
 * Vista del catálogo en el idioma actual. Si el id no está en `COMANDOS_TEXTO` se devuelve el
 * **mismo** objeto que sirvió el backend (respaldo seguro: nunca se inventa texto ni se traduce
 * el `id`, el `usage` o la sintaxis de los valores).
 */
export function comandoTraducido(
  comando: CommandSpecView,
  t: ReturnType<typeof useT>,
): CommandSpecView {
  const texto: TextoComando | undefined = COMANDOS_TEXTO[comando.id];
  if (!texto) return comando;
  return {
    ...comando,
    summary: t(texto.summary),
    category: t(texto.category),
    args: comando.args.map((arg) => {
      const campos = texto.args?.[arg.key];
      if (!campos) return arg;
      return {
        ...arg,
        label: t(campos.label),
        // Sólo se traduce el marcador si el backend trae uno: una cadena vacía no se toca.
        placeholder: campos.placeholder && arg.placeholder ? t(campos.placeholder) : arg.placeholder,
      };
    }),
  };
}
