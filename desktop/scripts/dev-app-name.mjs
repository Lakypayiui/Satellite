/**
 * En desarrollo, Electron se ejecuta desde `Electron.app`, así que macOS muestra
 * "Electron" en el Dock/menú. El nombre de producción (`productName`) sólo aplica
 * al paquete final. Este script, idempotente y sólo para macOS:
 *   1. renombra el bundle real a `Satellite.app`;
 *   2. deja un symlink `Electron.app -> Satellite.app` para que el paquete
 *      `electron` (y `npm run dev`/`npm start`) siga encontrando el binario;
 *   3. ajusta el nombre visible en Info.plist y re-registra el bundle.
 *
 * En Windows/Linux no hace falta: el nombre lo fijan `app.setName`,
 * `AppUserModelId` y `--class`.
 */

import { execFileSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  lstatSync,
  renameSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

if (process.platform !== "darwin") process.exit(0);

const require = createRequire(import.meta.url);
let executable = "";
try {
  // En Node (no Electron), require("electron") devuelve la ruta del ejecutable.
  executable = require("electron");
} catch {
  process.exit(0);
}
if (typeof executable !== "string" || !executable) process.exit(0);

const distDir = path.resolve(path.dirname(executable), "..", "..", "..");
const electronBundle = path.join(distDir, "Electron.app");
const satelliteBundle = path.join(distDir, "Satellite.app");

function isSymlink(target) {
  try {
    return lstatSync(target).isSymbolicLink();
  } catch {
    return false;
  }
}

// Reinstalar `electron` vuelve a crear Electron.app como directorio real: se
// reemplaza el Satellite.app viejo por el nuevo. Nunca se toca nada fuera de
// node_modules/electron/dist.
if (existsSync(electronBundle) && !isSymlink(electronBundle)) {
  rmSync(satelliteBundle, { recursive: true, force: true });
  renameSync(electronBundle, satelliteBundle);
}

if (!existsSync(electronBundle) && existsSync(satelliteBundle)) {
  symlinkSync("Satellite.app", electronBundle, "dir");
}

const bundle = existsSync(satelliteBundle) ? satelliteBundle : electronBundle;
const plist = path.join(bundle, "Contents", "Info.plist");
if (!existsSync(plist)) process.exit(0);

// Icono del bundle: macOS lo usa para el preview de la ventana minimizada, cuyo
// fondo es gris. Se instala el arte NEGRO (`icon-light.icns`) para que se vea;
// el icono del Dock no se toca: `app.dock.setIcon` lo elige en runtime según el tema.
const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const buildDir = path.resolve(scriptsDir, "..", "build");
let iconSource = path.join(buildDir, "icon-light.icns");
if (!existsSync(iconSource)) {
  iconSource = path.join(buildDir, "icon.icns");
}
if (!existsSync(iconSource)) {
  try {
    execFileSync(process.execPath, [path.join(scriptsDir, "icons.mjs")]);
    iconSource = path.join(buildDir, "icon-light.icns");
  } catch {
    // sin icono generado se conserva el del bundle
  }
}
const iconTarget = path.join(bundle, "Contents", "Resources", "Satellite.icns");
if (existsSync(iconSource)) {
  copyFileSync(iconSource, iconTarget);
}

const values = {
  CFBundleName: "Satellite",
  CFBundleDisplayName: "Satellite",
  CFBundleIdentifier: "ai.satellite.desktop",
  ...(existsSync(iconTarget) ? { CFBundleIconFile: "Satellite.icns" } : {}),
};

for (const [key, value] of Object.entries(values)) {
  try {
    const current = execFileSync(
      "/usr/libexec/PlistBuddy",
      ["-c", `Print :${key}`, plist],
      { encoding: "utf8" },
    ).trim();
    if (current === value) continue;
    execFileSync("/usr/libexec/PlistBuddy", ["-c", `Set :${key} ${value}`, plist]);
  } catch {
    try {
      execFileSync("/usr/libexec/PlistBuddy", [
        "-c",
        `Add :${key} string ${value}`,
        plist,
      ]);
    } catch {
      // Sin PlistBuddy no se toca nada; el paquete final sigue usando productName.
    }
  }
}

// LaunchServices cachea el nombre por ruta/bundle id; sin esto el Dock puede
// seguir mostrando "Electron" hasta reiniciar el sistema.
try {
  execFileSync(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
    ["-f", bundle],
  );
} catch {
  // best-effort
}
