/** Lanza la app empaquetada de desarrollo con el binario correcto por SO. */

import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { electronBinary } from "./electron-path.mjs";

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const hijo = spawn(electronBinary(), ["."], {
  cwd: raiz,
  env: process.env,
  stdio: "inherit",
});
hijo.on("exit", (codigo) => process.exit(codigo ?? 0));
