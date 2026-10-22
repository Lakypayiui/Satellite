/**
 * Políticas de navegación y enlaces externos del proceso principal.
 *
 * Son funciones puras para poder probarlas sin arrancar Electron. El preload se
 * ejecuta en cada navegación del `webContents`, así que una página remota que tomara
 * la ventana heredaría el puente IPC: la ventana sólo puede quedarse en la app.
 */

export interface EventoNavegacion {
  preventDefault(): void;
}

export interface DetallesVentanaNueva {
  url: string;
}

const PROTOCOLOS_EXTERNOS = new Set(["http:", "https:", "mailto:"]);

function parsear(url: string): URL | null {
  try {
    return new URL(url.trim());
  } catch {
    return null;
  }
}

/** ¿El destino sigue siendo la propia app (fichero empaquetado o dev server)? */
export function esNavegacionDeLaApp(destino: string, appUrl: string): boolean {
  const objetivo = parsear(destino);
  const base = parsear(appUrl);
  if (!objetivo || !base) return false;
  if (base.protocol === "file:") {
    return objetivo.protocol === "file:" && objetivo.pathname === base.pathname;
  }
  if (base.protocol !== "http:" && base.protocol !== "https:") return false;
  return objetivo.origin === base.origin;
}

/** Sólo se delegan al sistema enlaces con protocolos seguros. */
export function esUrlExternaPermitida(url: string): boolean {
  const parsed = parsear(url);
  return parsed !== null && PROTOCOLOS_EXTERNOS.has(parsed.protocol);
}

/** Cancela la navegación fuera de la app y abre el enlace en el navegador del sistema. */
export function manejadorNavegacion(
  appUrl: string,
  abrirExterno: (url: string) => void,
): (evento: EventoNavegacion, destino: string) => void {
  return (evento, destino) => {
    const url = typeof destino === "string" ? destino.trim() : "";
    if (esNavegacionDeLaApp(url, appUrl)) return;
    evento.preventDefault();
    if (esUrlExternaPermitida(url)) abrirExterno(url);
  };
}

/** Las ventanas nuevas nunca se crean dentro de la app; el enlace seguro sale fuera. */
export function manejadorVentanaNueva(
  abrirExterno: (url: string) => void,
): (detalles: DetallesVentanaNueva) => { action: "deny" } {
  return ({ url }) => {
    const destino = typeof url === "string" ? url.trim() : "";
    if (esUrlExternaPermitida(destino)) abrirExterno(destino);
    return { action: "deny" };
  };
}
