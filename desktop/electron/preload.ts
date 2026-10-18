/**
 * Puente entre la interfaz y el proceso principal. Lo único que se expone es esta API:
 * nada de Node en el renderer, nada de URLs ni de token al alcance de la interfaz.
 */

import { contextBridge, ipcRenderer } from "electron";

/**
 * Este preload corre en un renderer con `sandbox: true`, donde `require` sólo resuelve
 * `electron` y unos pocos built-ins: **no puede importar módulos locales**. Por eso es
 * autocontenido. El tema inicial se toma tal cual del argumento del proceso principal (que
 * siempre manda un id válido del catálogo) y, si faltara, se cae a «obsidiana»; el renderer
 * lo valida igualmente contra el catálogo compartido (`src/`).
 */

export interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; status: number };
}

export interface WindowState {
  maximized: boolean;
  fullscreen: boolean;
}

export interface ThemePreference {
  theme: string;
  text_intensity: string;
  chat_watermark: boolean;
  chat_watermark_opacity: number;
  language: string;
}

/** Lee un argumento `--nombre=valor` que el proceso principal añade al renderer. */
function argumento(nombre: string): string {
  const prefijo = `--${nombre}=`;
  const encontrado = process.argv.find((item) => item.startsWith(prefijo));
  return encontrado ? encontrado.slice(prefijo.length) : "";
}

const temaInicial = argumento("satellite-theme");
const intensidadInicial = argumento("satellite-text-intensity");
const marcaInicial = argumento("satellite-chat-watermark");
const marcaOpacidadInicial = argumento("satellite-chat-watermark-opacity");
const idiomaInicial = argumento("satellite-language");
const plataforma = argumento("satellite-platform") || process.platform;

const api = {
  /** Petición a la API local a través del proceso principal. */
  request: (ruta: string, options: { method?: string; body?: unknown } = {}) =>
    ipcRenderer.invoke("satellite:request", ruta, options) as Promise<Envelope<unknown>>,

  /** Eventos en vivo del runtime. Devuelve la función para dejar de escuchar. */
  subscribe: (after: number, onItem: (item: unknown) => void) => {
    const handler = (_evento: unknown, item: unknown) => onItem(item);
    ipcRenderer.on("satellite:event", handler);
    ipcRenderer.send("satellite:subscribe", after);
    return () => {
      ipcRenderer.removeListener("satellite:event", handler);
      ipcRenderer.send("satellite:unsubscribe");
    };
  },

  /** Aviso de que el backend se cayó o no arrancó, con el motivo. */
  onBackendDown: (onDown: (mensaje: string) => void) => {
    const handler = (_evento: unknown, mensaje: string) => onDown(mensaje);
    ipcRenderer.on("satellite:backend-down", handler);
    return () => ipcRenderer.removeListener("satellite:backend-down", handler);
  },

  restart: () => ipcRenderer.invoke("satellite:restart") as Promise<Envelope<unknown>>,
  pickFolder: () => ipcRenderer.invoke("satellite:pick-folder") as Promise<string>,
  info: () =>
    ipcRenderer.invoke("satellite:info") as Promise<{
      ready: boolean;
      repoRoot: string;
      dev: boolean;
    }>,

  /** Plataforma del proceso (`win32`, `darwin`, `linux`). */
  platform: plataforma,

  /** Controles de la titlebar propia (frame:false). Canales IPC limitados y validados. */
  windowControls: {
    minimize: () => ipcRenderer.send("satellite:window-minimize"),
    toggleMaximize: () => ipcRenderer.send("satellite:window-toggle-maximize"),
    close: () => ipcRenderer.send("satellite:window-close"),
    state: () => ipcRenderer.invoke("satellite:window-state") as Promise<WindowState>,
    onState: (onState: (state: WindowState) => void) => {
      const handler = (_evento: unknown, state: WindowState) => onState(state);
      ipcRenderer.on("satellite:window-state", handler);
      return () => ipcRenderer.removeListener("satellite:window-state", handler);
    },
  },

  /** Tema e intensidad persistidos; los cambios se validan en el proceso principal. */
  theme: {
    // El proceso principal siempre manda ids válidos; estos son sólo el respaldo.
    initial: temaInicial || "obsidiana",
    initialIntensity: intensidadInicial || "medio",
    // Marca de agua: "1"/"0" y la opacidad ya acotada por el proceso principal.
    initialWatermark: marcaInicial || "1",
    initialWatermarkOpacity: marcaOpacidadInicial || "0.06",
    // Idioma de la interfaz (por defecto «es»).
    initialLanguage: idiomaInicial || "es",
    get: () => ipcRenderer.invoke("satellite:preferences") as Promise<ThemePreference>,
    set: (theme: string) =>
      ipcRenderer.invoke("satellite:preferences-set-theme", theme) as Promise<ThemePreference>,
    setIntensity: (intensity: string) =>
      ipcRenderer.invoke(
        "satellite:preferences-set-text-intensity",
        intensity,
      ) as Promise<ThemePreference>,
    setWatermark: (visible: boolean) =>
      ipcRenderer.invoke(
        "satellite:preferences-set-chat-watermark",
        visible,
      ) as Promise<ThemePreference>,
    setWatermarkOpacity: (opacity: number) =>
      ipcRenderer.invoke(
        "satellite:preferences-set-chat-watermark-opacity",
        opacity,
      ) as Promise<ThemePreference>,
    setLanguage: (language: string) =>
      ipcRenderer.invoke(
        "satellite:preferences-set-language",
        language,
      ) as Promise<ThemePreference>,
  },
};

contextBridge.exposeInMainWorld("satellite", api);

export type SatelliteBridge = typeof api;
