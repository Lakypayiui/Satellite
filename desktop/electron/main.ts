/**
 * Proceso principal: crea la ventana, arranca el backend Python, guarda el token y
 * reenvía los eventos a la interfaz. La interfaz nunca habla con el backend directamente.
 */

import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  nativeImage,
  nativeTheme,
  type IpcMainEvent,
  type IpcMainInvokeEvent,
} from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";

import { ApiError, Backend, StreamItem } from "./backend";
import { ChatGPTAccount } from "./chatgpt";
import {
  DEFAULT_CHAT_WATERMARK,
  DEFAULT_CHAT_WATERMARK_OPACITY,
  DEFAULT_IDIOMA,
  DEFAULT_THEME,
  DEFAULT_TEXT_INTENSITY,
  clampWatermarkOpacity,
  isIdioma,
  isTextIntensity,
  isThemeId,
  themeById,
  type IdiomaId,
  type TextIntensityId,
  type ThemeId,
} from "./themes";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DEV_URL = process.env.VITE_DEV_SERVER_URL ?? "";
const CERRAR_BACKEND_MS = 2_500;
// Iconos de la app tomados de la carpeta de iconos del repo (arte original, sin retocar). El
// arte claro se usa con el sistema en oscuro y el oscuro con el sistema en claro; se reaplica
// en vivo cuando cambia el tema del SO (`nativeTheme`).
const ICONO_SO_OSCURO = path.join(REPO_ROOT, "Icono", "Ico_white");
const ICONO_SO_CLARO = path.join(REPO_ROOT, "Icono", "Ico_Black");

/** Ruta del icono que corresponde al tema actual del sistema operativo. */
function iconoSegunSistema(): string {
  return nativeTheme.shouldUseDarkColors ? ICONO_SO_OSCURO : ICONO_SO_CLARO;
}

/** Fija el icono en el dock (macOS) o la barra de tareas (Windows/Linux) según el SO. */
function aplicarIconoSistema(): void {
  const imagen = nativeImage.createFromPath(iconoSegunSistema());
  if (imagen.isEmpty()) return;
  if (process.platform === "darwin") app.dock?.setIcon(imagen);
  else ventana?.setIcon(imagen);
}

/** Rutas de la API que la interfaz puede pedir. Nada fuera de esta lista.
 *  `scripts/smoke.mjs` comprueba que esta lista cubre todo lo que llama `src/`. */
const RUTAS = [
  "/status",
  "/projects",
  "/sessions",
  "/messages",
  "/plan",
  "/approvals",
  "/events",
  "/tasks",
  "/jobs",
  "/git",
  "/worktrees",
  "/comments",
  "/queue",
  "/agents",
  "/skills",
  "/config",
  "/desktop-control",
  "/safe-proxy",
  "/providers",
  "/providers/claude-models",
  "/providers/claude-account",
  "/models",
  "/roles",
  "/doctor",
  "/setup",
  "/init",
  "/tools",
  "/context",
  "/commands",
  "/stats",
  "/snapshots",
  "/restore",
  "/reset",
  "/compact",
  "/curate",
  "/turns/cancel",
  "/shutdown",
];

let ventana: BrowserWindow | null = null;
let backend: Backend | null = null;
const chatgptAccount = new ChatGPTAccount();
let cortarFlujo: (() => void) | null = null;
// Arranque único: una petición que llegue antes del handshake espera a este intento en vez
// de fallar con "el backend no está en marcha" (era lo que dejaba la ventana bloqueada).
let arranque: Promise<Backend> | null = null;
let errorArranque = "";
// Tema activo: arranca con el valor persistido y se actualiza al cambiarlo desde Apariencia.
let temaActual: ThemeId = DEFAULT_THEME;
// Intensidad del blanco (sólo la usa Deep Dark): igual, persistida y restaurada.
let intensidadActual: TextIntensityId = DEFAULT_TEXT_INTENSITY;
// Marca de agua del chat: visibilidad y opacidad elegidas por la persona (mismo archivo).
let marcaActual: boolean = DEFAULT_CHAT_WATERMARK;
let marcaOpacidadActual: number = DEFAULT_CHAT_WATERMARK_OPACITY;
// Idioma de la interfaz (español por defecto; no cambia formatos ni zonas horarias).
let idiomaActual: IdiomaId = DEFAULT_IDIOMA;

/**
 * Preferencias del usuario en `userData/preferences.json` (p. ej. `%APPDATA%/satellite-desktop`).
 * Es configuración del usuario, no del proyecto, y no toca la configuración del backend.
 */
function rutaPreferencias(): string {
  return path.join(app.getPath("userData"), "preferences.json");
}

function leerPreferencias(): Record<string, unknown> {
  try {
    const data: unknown = JSON.parse(readFileSync(rutaPreferencias(), "utf-8"));
    return data && typeof data === "object" && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function leerTema(): ThemeId {
  const data = leerPreferencias();
  return isThemeId(data.theme) ? data.theme : DEFAULT_THEME;
}

function leerIntensidad(): TextIntensityId {
  const data = leerPreferencias();
  return isTextIntensity(data.text_intensity) ? data.text_intensity : DEFAULT_TEXT_INTENSITY;
}

/** Visibilidad de la marca de agua: por defecto visible (compatible con prefs previas). */
function leerMarca(): boolean {
  const data = leerPreferencias();
  return typeof data.chat_watermark === "boolean" ? data.chat_watermark : DEFAULT_CHAT_WATERMARK;
}

/** Opacidad de la marca de agua, acotada al rango válido. */
function leerMarcaOpacidad(): number {
  const data = leerPreferencias();
  return clampWatermarkOpacity(
    data.chat_watermark_opacity ?? DEFAULT_CHAT_WATERMARK_OPACITY,
  );
}

/** Idioma de la interfaz: español si no hay preferencia guardada. */
function leerIdioma(): IdiomaId {
  const data = leerPreferencias();
  return isIdioma(data.language) ? data.language : DEFAULT_IDIOMA;
}

/** Vista completa de las preferencias de aspecto (una sola forma para todas las respuestas). */
function preferenciasActuales(): Record<string, unknown> {
  return {
    theme: temaActual,
    text_intensity: intensidadActual,
    chat_watermark: marcaActual,
    chat_watermark_opacity: marcaOpacidadActual,
    language: idiomaActual,
  };
}

/** Mezcla claves en `preferences.json` sin pisar el resto (compatibilidad hacia atrás). */
function guardarPreferencias(parcial: Record<string, unknown>): void {
  try {
    const ruta = rutaPreferencias();
    mkdirSync(path.dirname(ruta), { recursive: true });
    writeFileSync(ruta, JSON.stringify({ ...leerPreferencias(), ...parcial }, null, 2), "utf-8");
  } catch {
    // Si no se puede escribir, el cambio sigue aplicado en esta sesión.
  }
}

function pythonPath(): string {
  if (process.env.SATELLITE_PYTHON) return process.env.SATELLITE_PYTHON;
  return process.platform === "win32"
    ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    : path.join(REPO_ROOT, ".venv", "bin", "python");
}

function permitida(ruta: string): boolean {
  return RUTAS.some(
    (prefijo) => ruta === prefijo || ruta.startsWith(`${prefijo}/`) || ruta.startsWith(`${prefijo}?`),
  );
}

function avisarBackendCaido(mensaje: string): void {
  errorArranque = mensaje;
  ventana?.webContents.send("satellite:backend-down", mensaje);
}

/** Arranca el backend una sola vez; quien pregunte a la vez comparte el mismo intento. */
function iniciarBackend(): Promise<Backend> {
  if (backend?.ready) return Promise.resolve(backend);
  if (!arranque) {
    arranque = (async () => {
      const broker = await chatgptAccount.startBroker();
      const instancia = new Backend(REPO_ROOT, pythonPath(), process.env.SATELLITE_HOME, broker);
      await instancia.start(process.env.SATELLITE_PROJECT || undefined);
      backend = instancia;
      errorArranque = "";
      if (ventana) suscribir();
      return instancia;
    })()
      .catch((exc: unknown) => {
        avisarBackendCaido(String(exc));
        throw exc;
      })
      .finally(() => {
        arranque = null;
      });
  }
  return arranque;
}

async function arrancarBackend(): Promise<void> {
  await iniciarBackend();
}

function suscribir(): void {
  cortarFlujo?.();
  cortarFlujo = null;
  if (!backend?.ready) {
    // El backend aún no está listo: se engancha al terminar (una sola vez).
    void iniciarBackend()
      .then(() => {
        if (ventana && !cortarFlujo) suscribir();
      })
      .catch(() => {});
    return;
  }
  if (!ventana) return;
  cortarFlujo = backend.stream(
    0,
    (item: StreamItem) => ventana?.webContents.send("satellite:event", item),
    (mensaje: string) => ventana?.webContents.send("satellite:backend-down", mensaje),
  );
}

function createWindow(): void {
  const tema = themeById(temaActual);
  ventana = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 680,
    backgroundColor: tema.background,
    title: "Satellite",
    // Icono en dock/barra de tareas según el tema del sistema (macOS usa `app.dock.setIcon`).
    icon: iconoSegunSistema(),
    show: false,
    // Titlebar propia: sin frame nativo (nada blanco del chrome del sistema). Los controles
    // los dibuja el renderer y actúan por IPC validado.
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // Tema, intensidad y plataforma accesibles de forma síncrona en el preload
      // (evita el flash de tema) .
      additionalArguments: [
        `--satellite-theme=${tema.id}`,
        `--satellite-text-intensity=${intensidadActual}`,
        `--satellite-chat-watermark=${marcaActual ? "1" : "0"}`,
        `--satellite-chat-watermark-opacity=${marcaOpacidadActual}`,
        `--satellite-language=${idiomaActual}`,
        `--satellite-platform=${process.platform}`,
      ],
    },
  });
  const enviarEstado = (): void => {
    if (!ventana) return;
    ventana.webContents.send("satellite:window-state", {
      maximized: ventana.isMaximized(),
      fullscreen: ventana.isFullScreen(),
    });
  };
  ventana.on("maximize", enviarEstado);
  ventana.on("unmaximize", enviarEstado);
  ventana.on("enter-full-screen", enviarEstado);
  ventana.on("leave-full-screen", enviarEstado);
  ventana.once("ready-to-show", () => ventana?.show());
  ventana.on("closed", () => {
    ventana = null;
  });
  // Si el backend falló antes de que la ventana estuviera lista, el aviso se habría perdido:
  // se repite aquí para que aparezca el banner con «reintentar». También manda el estado inicial
  // de la ventana para que la titlebar pinte maximizar/restaurar correcto.
  ventana.webContents.once("did-finish-load", () => {
    if (errorArranque) ventana?.webContents.send("satellite:backend-down", errorArranque);
    enviarEstado();
  });
  if (DEV_URL) {
    void ventana.loadURL(DEV_URL);
  } else {
    void ventana.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

ipcMain.handle(
  "satellite:request",
  async (_evento, ruta: string, options: { method?: string; body?: unknown }) => {
    try {
      if (typeof ruta !== "string" || !permitida(ruta)) {
        throw new ApiError("forbidden_path", `ruta no permitida: ${String(ruta)}`, 400);
      }
      // La interfaz puede preguntar antes de que el backend haya terminado de arrancar:
      // se espera al handshake en vez de responder "el backend no está en marcha".
      let instancia: Backend;
      try {
        instancia = backend?.ready ? backend : await iniciarBackend();
      } catch (exc) {
        throw new ApiError("backend_down", String(exc), 0);
      }
      const data = await instancia.request<unknown>(ruta, options ?? {});
      return { ok: true, data };
    } catch (exc) {
      const error =
        exc instanceof ApiError
          ? { code: exc.code, message: exc.message, status: exc.status }
          : { code: "unexpected", message: String(exc), status: 500 };
      return { ok: false, error };
    }
  },
);

ipcMain.handle("satellite:info", () => ({
  ready: backend?.ready ?? false,
  repoRoot: REPO_ROOT,
  dev: Boolean(DEV_URL),
}));

ipcMain.handle("satellite:chatgpt-status", (evento) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  return chatgptAccount.status();
});

ipcMain.handle("satellite:chatgpt-login", async (evento) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  return chatgptAccount.signIn();
});

ipcMain.handle("satellite:chatgpt-disconnect", (evento) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  chatgptAccount.disconnect();
  return chatgptAccount.status();
});

ipcMain.handle("satellite:claude-login", (evento) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  // Claude Code is Anthropic's supported account-authenticated route for plan users.
  // Run its normal interactive login in a visible terminal; credentials stay in Claude Code.
  let child: ChildProcess;
  if (process.platform === "win32") {
    child = spawn("cmd.exe", ["/d", "/c", "start", "", "cmd.exe", "/k", "claude"], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    });
  } else if (process.platform === "darwin") {
    child = spawn("osascript", ["-e", 'tell application "Terminal" to do script "claude"'], {
      detached: true,
      stdio: "ignore",
    });
  } else {
    const terminal = ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal"]
      .find((command) => Boolean(process.env.PATH?.split(path.delimiter).some((dir) =>
        existsSync(path.join(dir, command)),
      )));
    if (!terminal) return { launched: false, message: "No se encontró una terminal disponible." };
    const args = terminal === "gnome-terminal" ? ["--", "claude"] : ["-e", "claude"];
    child = spawn(terminal, args, { detached: true, stdio: "ignore" });
  }
  child.once("error", (error) => {
    console.error("No se pudo abrir Claude Code:", error.message);
  });
  child.unref();
  return { launched: true };
});

ipcMain.handle("satellite:pick-folder", async () => {
  const resultado = await dialog.showOpenDialog({
    title: "Abrir carpeta de proyecto",
    properties: ["openDirectory", "createDirectory"],
  });
  return resultado.canceled ? "" : (resultado.filePaths[0] ?? "");
});

/** Solo la ventana de la app puede usar los canales de ventana y preferencias. */
function remitenteValido(evento: IpcMainEvent | IpcMainInvokeEvent): boolean {
  return Boolean(ventana && evento.sender === ventana.webContents);
}

ipcMain.handle("satellite:window-state", (evento) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  return {
    maximized: ventana?.isMaximized() ?? false,
    fullscreen: ventana?.isFullScreen() ?? false,
  };
});

ipcMain.on("satellite:window-minimize", (evento) => {
  if (remitenteValido(evento)) ventana?.minimize();
});

ipcMain.on("satellite:window-toggle-maximize", (evento) => {
  if (!remitenteValido(evento) || !ventana) return;
  if (ventana.isMaximized()) ventana.unmaximize();
  else ventana.maximize();
});

ipcMain.on("satellite:window-close", (evento) => {
  if (remitenteValido(evento)) ventana?.close();
});

ipcMain.handle("satellite:preferences", (evento) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  return preferenciasActuales();
});

ipcMain.handle("satellite:preferences-set-theme", (evento, tema: unknown) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  if (!isThemeId(tema)) throw new Error("tema desconocido");
  temaActual = tema;
  guardarPreferencias({ theme: tema });
  ventana?.setBackgroundColor(themeById(tema).background);
  return preferenciasActuales();
});

ipcMain.handle("satellite:preferences-set-text-intensity", (evento, intensidad: unknown) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  if (!isTextIntensity(intensidad)) throw new Error("intensidad desconocida");
  intensidadActual = intensidad;
  guardarPreferencias({ text_intensity: intensidad });
  return preferenciasActuales();
});

ipcMain.handle("satellite:preferences-set-chat-watermark", (evento, activa: unknown) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  if (typeof activa !== "boolean") throw new Error("valor de marca de agua inválido");
  marcaActual = activa;
  guardarPreferencias({ chat_watermark: activa });
  return preferenciasActuales();
});

ipcMain.handle("satellite:preferences-set-chat-watermark-opacity", (evento, valor: unknown) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  marcaOpacidadActual = clampWatermarkOpacity(valor);
  guardarPreferencias({ chat_watermark_opacity: marcaOpacidadActual });
  return preferenciasActuales();
});

ipcMain.handle("satellite:preferences-set-language", (evento, idioma: unknown) => {
  if (!remitenteValido(evento)) throw new Error("remitente no autorizado");
  if (!isIdioma(idioma)) throw new Error("idioma desconocido");
  idiomaActual = idioma;
  guardarPreferencias({ language: idioma });
  return preferenciasActuales();
});

ipcMain.handle("satellite:restart", async () => {
  try {
    await arrancarBackend();
    return { ok: true, data: { ready: true } };
  } catch (exc) {
    return { ok: false, error: { code: "backend_down", message: String(exc), status: 0 } };
  }
});

ipcMain.on("satellite:subscribe", () => suscribir());
ipcMain.on("satellite:unsubscribe", () => {
  cortarFlujo?.();
  cortarFlujo = null;
});

void app.whenReady().then(() => {
  temaActual = leerTema();
  intensidadActual = leerIntensidad();
  marcaActual = leerMarca();
  marcaOpacidadActual = leerMarcaOpacidad();
  idiomaActual = leerIdioma();
  createWindow();
  // El icono sigue el tema del sistema y se actualiza en vivo si este cambia.
  nativeTheme.on("updated", aplicarIconoSistema);
  aplicarIconoSistema();
  // El arranque avisa por sí mismo si falla (banner con «reintentar»); las peticiones de la
  // interfaz esperan a este mismo intento, así que no hay carrera con el handshake.
  void arrancarBackend().catch(() => {});
  app.on("activate", () => {
    if (!ventana) createWindow();
  });
});

app.on("window-all-closed", () => app.quit());

app.on("before-quit", (evento) => {
  const instancia = backend;
  if (!instancia) return;
  evento.preventDefault();
  backend = null;
  cortarFlujo?.();
  cortarFlujo = null;
  const timeout = setTimeout(() => app.exit(0), CERRAR_BACKEND_MS);
  void instancia.stop().finally(() => {
    clearTimeout(timeout);
    app.quit();
  });
});
