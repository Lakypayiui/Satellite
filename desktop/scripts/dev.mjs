/**
 * Desarrollo: servidor de Vite (recarga en caliente) + Electron apuntando a él.
 * Sin dependencias extra: Vite se arranca por su API y Electron con su binario.
 */

import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

import { electronBinary } from "./electron-path.mjs";

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const vite = await createServer({ configFile: path.join(raiz, "vite.config.ts") });
await vite.listen();
const url = vite.resolvedUrls?.local?.[0] ?? "http://localhost:5173";
console.log(`[dev] interfaz en ${url}`);

const electron = spawn(electronBinary(), ["."], {
  cwd: raiz,
  env: { ...process.env, VITE_DEV_SERVER_URL: url },
  stdio: "inherit",
});

async function cerrar(codigo) {
  await vite.close();
  process.exit(codigo ?? 0);
}

electron.on("exit", (codigo) => void cerrar(codigo ?? 0));
process.on("SIGINT", () => {
  electron.kill();
  void cerrar(0);
});
