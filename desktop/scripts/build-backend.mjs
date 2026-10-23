/**
 * Congela el backend de Python (CLI, TUI, MCP y backend del escritorio) en un único
 * ejecutable con PyInstaller: `../dist-backend/satellite/`. electron-builder lo incluye
 * como `resources/backend` (ver `build.extraResources` en package.json).
 *
 * Python: `SATELLITE_PYTHON`, o el `.venv` del repo (dentro o junto a él), o
 * `python3`/`python` del PATH. Tiene
 * que tener instalados el paquete (`pip install ".[desktop]"`) y `pyinstaller`.
 * PyInstaller no compila para otro sistema: cada paquete se genera en su sistema.
 */

import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const enVenv = (base) =>
  process.platform === "win32"
    ? path.join(base, ".venv", "Scripts", "python.exe")
    : path.join(base, ".venv", "bin", "python");
// El `.venv` puede estar dentro del repo o junto a él (un nivel por encima).
const venv = [enVenv(repo), enVenv(path.dirname(repo))].find((candidate) => existsSync(candidate));
const python =
  process.env.SATELLITE_PYTHON || venv || (process.platform === "win32" ? "python" : "python3");

const result = spawnSync(
  python,
  [
    "-m",
    "PyInstaller",
    path.join("packaging", "satellite.spec"),
    "--noconfirm",
    "--distpath",
    "dist-backend",
    "--workpath",
    path.join("build", "pyinstaller"),
  ],
  { cwd: repo, stdio: "inherit" },
);
if (result.status !== 0) {
  console.error(`[build-backend] PyInstaller falló (python: ${python})`);
  process.exit(result.status ?? 1);
}
const exe = path.join(
  repo,
  "dist-backend",
  "satellite",
  process.platform === "win32" ? "satellite.exe" : "satellite",
);
if (!existsSync(exe)) {
  console.error(`[build-backend] no se generó ${exe}`);
  process.exit(1);
}
const check = spawnSync(exe, ["--help"], { encoding: "utf8" });
if (check.status !== 0) {
  console.error(`[build-backend] el ejecutable no arranca:\n${check.stderr}`);
  process.exit(1);
}
console.log(`[build-backend] listo: ${exe}`);
