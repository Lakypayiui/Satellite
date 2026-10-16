/**
 * Puente entre la interfaz y el proceso principal. Lo único que se expone es esta API:
 * nada de Node en el renderer, nada de URLs ni de token al alcance de la interfaz.
 */

import { contextBridge, ipcRenderer } from "electron";

export interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; status: number };
}

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
};

contextBridge.exposeInMainWorld("satellite", api);

export type SatelliteBridge = typeof api;
