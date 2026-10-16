/**
 * Proceso principal: crea la ventana, arranca el backend Python, guarda el token y
 * reenvía los eventos a la interfaz. La interfaz nunca habla con el backend directamente.
 */

import { app, BrowserWindow, dialog, ipcMain } from "electron";
import path from "node:path";

import { ApiError, Backend, StreamItem } from "./backend";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DEV_URL = process.env.VITE_DEV_SERVER_URL ?? "";
const CERRAR_BACKEND_MS = 2_500;

/** Rutas de la API que la interfaz puede pedir. Nada fuera de esta lista. */
const RUTAS = [
  "/status",
  "/projects",
  "/sessions",
  "/messages",
  "/approvals",
  "/events",
  "/tasks",
  "/jobs",
  "/agents",
  "/skills",
  "/config",
  "/providers",
  "/models",
  "/shutdown",
];

let ventana: BrowserWindow | null = null;
let backend: Backend | null = null;
let cortarFlujo: (() => void) | null = null;

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

function suscribir(): void {
  cortarFlujo?.();
  cortarFlujo = null;
  if (!backend || !ventana) return;
  cortarFlujo = backend.stream(
    0,
    (item: StreamItem) => ventana?.webContents.send("satellite:event", item),
    (mensaje: string) => ventana?.webContents.send("satellite:backend-down", mensaje),
  );
}

async function arrancarBackend(): Promise<void> {
  const instancia = new Backend(REPO_ROOT, pythonPath(), process.env.SATELLITE_HOME);
  await instancia.start(process.env.SATELLITE_PROJECT || undefined);
  backend = instancia;
  suscribir();
}

function createWindow(): void {
  ventana = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 680,
    backgroundColor: "#0f1419",
    title: "Satellite",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  ventana.once("ready-to-show", () => ventana?.show());
  ventana.on("closed", () => {
    ventana = null;
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
      if (!backend) throw new ApiError("backend_down", "el backend no está en marcha", 0);
      if (typeof ruta !== "string" || !permitida(ruta)) {
        throw new ApiError("forbidden_path", `ruta no permitida: ${String(ruta)}`, 400);
      }
      const data = await backend.request<unknown>(ruta, options ?? {});
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

ipcMain.handle("satellite:pick-folder", async () => {
  const resultado = await dialog.showOpenDialog({
    title: "Abrir carpeta de proyecto",
    properties: ["openDirectory", "createDirectory"],
  });
  return resultado.canceled ? "" : (resultado.filePaths[0] ?? "");
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

void app.whenReady().then(async () => {
  createWindow();
  try {
    await arrancarBackend();
  } catch (exc) {
    ventana?.webContents.send("satellite:backend-down", String(exc));
  }
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
