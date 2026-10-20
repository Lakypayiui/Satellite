/**
 * Ruta del binario de Electron para desarrollo.
 *
 * En macOS, `scripts/dev-app-name.mjs` renombra el bundle real a `Satellite.app`
 * (y deja `Electron.app` como symlink para el paquete npm). Lanzar por la ruta
 * real hace que LaunchServices/Dock identifiquen la app como Satellite; por el
 * symlink el bundle path seguía siendo Electron.app.
 */

import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);

export function electronBinary() {
  const base = require("electron");
  if (process.platform !== "darwin") return base;
  // .../dist/Electron.app/Contents/MacOS/Electron -> .../dist
  const dist = path.resolve(path.dirname(base), "..", "..", "..");
  const satellite = path.join(
    dist,
    "Satellite.app",
    "Contents",
    "MacOS",
    "Electron",
  );
  return existsSync(satellite) ? satellite : base;
}
