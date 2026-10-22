/**
 * Comprobación de extremo a extremo sin interfaz gráfica.
 *
 * Ejercita exactamente el camino que usa Electron: arrancar `python -m satellite.desktop`,
 * entregarle el token por stdin, leer sólo el puerto del handshake, exigir el token,
 * hablar con la API y apagar el proceso. Si esto pasa, lo que falla (si falla) es la
 * ventana, no la integración.
 */

import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const python =
  process.env.SATELLITE_PYTHON ??
  (process.platform === "win32"
    ? path.join(raiz, ".venv", "Scripts", "python.exe")
    : path.join(raiz, ".venv", "bin", "python"));

const proyectoPropio = !process.env.SATELLITE_PROJECT;
const proyecto = process.env.SATELLITE_PROJECT ?? mkdtempSync(path.join(tmpdir(), "satellite-smoke-"));

let fallos = 0;
function comprobar(nombre, ok, detalle = "") {
  console.log(`${ok ? "  ok  " : " FALLO"} ${nombre}${detalle ? ` — ${detalle}` : ""}`);
  if (!ok) fallos += 1;
}

const token = randomBytes(32).toString("hex");
const hijo = spawn(
  python,
  ["-m", "satellite.desktop", "--project", proyecto, "--token-stdin"],
  {
    cwd: raiz,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  },
);
let errorSalida = "";
let salidaEstandar = "";
hijo.stdout.on("data", (t) => {
  salidaEstandar += t.toString();
});
hijo.stderr.on("data", (t) => {
  errorSalida += t.toString();
});
hijo.stdin.on("error", () => {
  // Si el backend sale antes de leerlo, el handshake de abajo da el error real.
});
hijo.stdin.end(`${token}\n`);

const port = await new Promise((resolve, reject) => {
  const lineas = createInterface({ input: hijo.stdout });
  const timer = setTimeout(() => reject(new Error("sin handshake en 20 s")), 20_000);
  lineas.on("line", (linea) => {
    if (linea.includes(token)) {
      clearTimeout(timer);
      lineas.close();
      reject(new Error("el token apareció en stdout"));
      return;
    }
    if (!linea.trim().startsWith("{")) return;
    try {
      const data = JSON.parse(linea);
      if (typeof data.port === "number") {
        clearTimeout(timer);
        lineas.close();
        resolve(data.port);
      }
    } catch {
      // no es el handshake
    }
  });
  hijo.on("error", (exc) => {
    clearTimeout(timer);
    lineas.close();
    reject(new Error(`no se pudo arrancar ${python}: ${exc.message}`));
  });
  hijo.on("exit", (codigo) => {
    clearTimeout(timer);
    lineas.close();
    reject(new Error(`el backend salió con ${codigo}: ${errorSalida.trim()}`));
  });
});

const handshake = { port, token };
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
  comprobar("el token no aparece en stdout", !salidaEstandar.includes(token));

  const sesiones = await json("/api/sessions", { headers: conToken });
  comprobar("lista de sesiones", sesiones.estado === 200 && Array.isArray(sesiones.data.sessions));
  comprobar("la sesión por defecto existe desde el principio", Boolean(sesiones.data.current));

  // The chat shows diagrams by fetching their SVG from the project.
  const diagramas = path.join(proyecto, "diagrams");
  const diagramaArchivo = path.join(diagramas, `smoke-${process.pid}.svg`);
  mkdirSync(diagramas, { recursive: true });
  writeFileSync(
    diagramaArchivo,
    '<svg xmlns="http://www.w3.org/2000/svg"><text>ok</text></svg>',
    "utf8",
  );
  const diagrama = await json(
    `/api/diagrams?path=${encodeURIComponent(path.relative(proyecto, diagramaArchivo))}`,
    { headers: conToken },
  );
  rmSync(diagramaArchivo, { force: true });
  comprobar(
    "diagrama SVG servido",
    diagrama.estado === 200 && String(diagrama.data.svg ?? "").includes("<svg"),
  );

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
  // The smoke creates its own temp project: delete it instead of leaving
  // satellite-smoke-* directories behind in the OS temp folder.
  if (proyectoPropio) {
    rmSync(proyecto, { recursive: true, force: true });
  }
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

// --- apariencia: titlebar propia (sin frame nativo) y catálogo de temas ---
{
  const { readFileSync } = await import("node:fs");
  const main = readFileSync(path.join(raiz, "desktop", "electron", "main.ts"), "utf8");
  comprobar("la ventana es frameless (titlebar propia)", /frame:\s*false/.test(main));
  const canales = [
    "satellite:window-minimize",
    "satellite:window-toggle-maximize",
    "satellite:window-close",
    "satellite:window-state",
    "satellite:preferences",
    "satellite:preferences-set-theme",
  ];
  comprobar(
    "IPC de ventana y preferencias definido",
    canales.every((canal) => main.includes(canal)),
  );

  const preload = readFileSync(path.join(raiz, "desktop", "electron", "preload.ts"), "utf8");
  comprobar(
    "el preload expone windowControls y theme",
    preload.includes("windowControls:") && preload.includes("theme:"),
  );

  const temas = readFileSync(path.join(raiz, "desktop", "electron", "themes.ts"), "utf8");
  const ids = ["obsidiana", "grafito", "medianoche", "bosque", "claro", "deepdark"];
  comprobar(
    "catálogo de temas completo",
    ids.every((id) => temas.includes(`"${id}"`)),
    ids.join(", "),
  );
  comprobar("tema por defecto obsidiana", /DEFAULT_THEME:\s*ThemeId\s*=\s*"obsidiana"/.test(temas));

  // Deep Dark: intensidad del blanco (4 niveles, por defecto «medio»).
  const niveles = ["suave", "medio", "brillante", "maximo"];
  comprobar(
    "niveles de intensidad definidos",
    niveles.every((nivel) => temas.includes(`id: "${nivel}"`)),
    niveles.join(", "),
  );
  comprobar(
    "intensidad por defecto media",
    /DEFAULT_TEXT_INTENSITY:\s*TextIntensityId\s*=\s*"medio"/.test(temas),
  );
  comprobar(
    "IPC de intensidad definido",
    main.includes("satellite:preferences-set-text-intensity"),
  );
  comprobar(
    "el preload expone la intensidad",
    preload.includes("initialIntensity") && preload.includes("setIntensity"),
  );

  const html = readFileSync(path.join(raiz, "desktop", "index.html"), "utf8");
  comprobar(
    "la CSP permite los SVG en data: del chat",
    /img-src[^;]*data:/.test(html),
  );

  const estilos = readFileSync(path.join(raiz, "desktop", "src", "styles.css"), "utf8");
  comprobar(
    "Deep Dark con fondo negro puro",
    /\[data-theme="deepdark"\][\s\S]*?--bg: #000000/.test(estilos),
  );
  comprobar(
    "intensidad en CSS (gris claro → blanco puro)",
    niveles.every((nivel) => estilos.includes(`[data-theme="deepdark"][data-intensidad="${nivel}"]`)) &&
      estilos.includes("--text: #ffffff"),
  );
}

console.log(fallos === 0 ? "\nTODO CORRECTO" : `\n${fallos} comprobación(es) fallidas`);
process.exit(fallos === 0 ? 0 : 1);
