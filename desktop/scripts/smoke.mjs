/**
 * Comprobación de extremo a extremo sin interfaz gráfica.
 *
 * Ejercita exactamente el camino que usa Electron: arrancar `python -m satellite.desktop`,
 * leer el handshake, exigir el token, hablar con la API y apagar el proceso. Si esto pasa,
 * lo que falla (si falla) es la ventana, no la integración.
 */

import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const python =
  process.env.SATELLITE_PYTHON ??
  (process.platform === "win32"
    ? path.join(raiz, ".venv", "Scripts", "python.exe")
    : path.join(raiz, ".venv", "bin", "python"));

const proyecto = process.env.SATELLITE_PROJECT ?? mkdtempSync(path.join(tmpdir(), "satellite-smoke-"));

let fallos = 0;
function comprobar(nombre, ok, detalle = "") {
  console.log(`${ok ? "  ok  " : " FALLO"} ${nombre}${detalle ? ` — ${detalle}` : ""}`);
  if (!ok) fallos += 1;
}

const hijo = spawn(python, ["-m", "satellite.desktop", "--project", proyecto], {
  cwd: raiz,
  env: { ...process.env, PYTHONUNBUFFERED: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let errorSalida = "";
hijo.stderr.on("data", (t) => {
  errorSalida += t.toString();
});

const handshake = await new Promise((resolve, reject) => {
  const lineas = createInterface({ input: hijo.stdout });
  const timer = setTimeout(() => reject(new Error("sin handshake en 20 s")), 20_000);
  lineas.on("line", (linea) => {
    if (!linea.trim().startsWith("{")) return;
    try {
      const data = JSON.parse(linea);
      if (typeof data.port === "number" && typeof data.token === "string") {
        clearTimeout(timer);
        resolve(data);
      }
    } catch {
      // no es el handshake
    }
  });
  hijo.on("exit", (codigo) => reject(new Error(`el backend salió con ${codigo}: ${errorSalida.trim()}`)));
});

const base = `http://127.0.0.1:${handshake.port}`;
const conToken = { Authorization: `Bearer ${handshake.token}` };

async function json(ruta, opciones = {}) {
  const respuesta = await fetch(`${base}${ruta}`, opciones);
  const texto = await respuesta.text();
  let data = {};
  try {
    data = texto ? JSON.parse(texto) : {};
  } catch {
    data = { crudo: texto.slice(0, 200) };
  }
  return { estado: respuesta.status, data, cabeceras: respuesta.headers };
}

try {
  console.log(`backend en el puerto ${handshake.port}, proyecto ${proyecto}`);

  const salud = await json("/api/health");
  comprobar("health responde sin token", salud.estado === 200 && salud.data.ok === true);

  const sinToken = await json("/api/status");
  comprobar("status sin token da 401", sinToken.estado === 401, `código ${sinToken.estado}`);
  comprobar(
    "el token no aparece en la respuesta",
    !JSON.stringify(sinToken.data).includes(handshake.token),
  );

  const estado = await json("/api/status", { headers: conToken });
  comprobar("status con token da 200", estado.estado === 200, `código ${estado.estado}`);
  comprobar("dice si el runtime está listo", typeof estado.data.runtime_ready === "boolean");

  const sesiones = await json("/api/sessions", { headers: conToken });
  comprobar("lista de sesiones", sesiones.estado === 200 && Array.isArray(sesiones.data.sessions));
  comprobar("la sesión por defecto existe desde el principio", Boolean(sesiones.data.current));

  const tareas = await json("/api/tasks", { headers: conToken });
  comprobar("lista de tareas", tareas.estado === 200 && Array.isArray(tareas.data.tasks));

  const trabajos = await json("/api/jobs", { headers: conToken });
  comprobar("lista de trabajos", trabajos.estado === 200 && Array.isArray(trabajos.data.jobs));

  const proveedores = await json("/api/providers", { headers: conToken });
  comprobar(
    "los proveedores no llevan la clave",
    proveedores.estado === 200 && !JSON.stringify(proveedores.data).includes("api_key\":"),
  );

  const config = await json("/api/config", { headers: conToken });
  comprobar("configuración legible", config.estado === 200 && typeof config.data.policy_mode === "string");

  const aprobaciones = await json("/api/approvals", { headers: conToken });
  comprobar(
    "permisos pendientes legibles",
    aprobaciones.estado === 200 && Array.isArray(aprobaciones.data.pending),
  );

  const inexistente = await json("/api/tasks/no-existe", { headers: conToken });
  comprobar(
    "una tarea inexistente se explica",
    inexistente.estado === 404 && inexistente.data.error?.code === "task_not_found",
  );

  const controlador = new AbortController();
  const flujo = await fetch(`${base}/api/events?after=0`, { headers: conToken, signal: controlador.signal });
  comprobar(
    "el flujo SSE se abre con el tipo correcto",
    flujo.status === 200 && (flujo.headers.get("content-type") ?? "").includes("text/event-stream"),
  );
  controlador.abort();

  const apagado = await json("/api/shutdown", { method: "POST", headers: conToken });
  comprobar("apagado ordenado aceptado", apagado.estado === 200 && apagado.data.closing === true);
} catch (exc) {
  comprobar("recorrido completo", false, String(exc));
} finally {
  hijo.kill();
  await new Promise((resolve) => setTimeout(resolve, 300));
}

// --- coherencia: lo que llama la interfaz tiene que estar permitido en el proceso principal ---
{
  const { readFileSync, readdirSync } = await import("node:fs");
  const fuente = readFileSync(path.join(raiz, "desktop", "electron", "main.ts"), "utf8");
  const bloque = fuente.match(/const RUTAS = \[([\s\S]*?)\];/);
  const permitidas = [...(bloque?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  const llamadas = new Set();
  for (const fichero of readdirSync(path.join(raiz, "desktop", "src"))) {
    if (!/\.tsx?$/.test(fichero)) continue;
    const texto = readFileSync(path.join(raiz, "desktop", "src", fichero), "utf8");
    for (const coincidencia of texto.matchAll(/api(?:<[^>]*>)?\(\s*[`"](\/[a-z0-9/_-]*)/g)) {
      llamadas.add(coincidencia[1]);
    }
  }
  const sinPermiso = [...llamadas].filter(
    (ruta) => !permitidas.some((permitida) => ruta === permitida || ruta.startsWith(`${permitida}/`)),
  );
  comprobar(
    "la lista de rutas de Electron cubre lo que llama la interfaz",
    sinPermiso.length === 0,
    sinPermiso.length ? `faltan: ${sinPermiso.join(", ")}` : `${llamadas.size} rutas`,
  );
}

console.log(fallos === 0 ? "\nTODO CORRECTO" : `\n${fallos} comprobación(es) fallidas`);
process.exit(fallos === 0 ? 0 : 1);
