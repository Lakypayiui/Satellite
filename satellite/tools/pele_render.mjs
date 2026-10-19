// Pele render helper (bundled with Satellite).
//
// Pele (https://github.com/obsidianmd/pele, npm "pele", MIT) is a JS library with no
// CLI: the official supported method is `import { render } from 'pele'`. This tiny
// script is the only piece of Node code Satellite ships; it never installs anything.
// It reads a JSON request on stdin and writes a JSON response on stdout.
//
// Request:  { "module": "<abs path to pele dist/pele.js>", "source": "<mermaid>",
//             "outPath": "<optional svg path>", "options": { ...RenderOptions } }
// Response: { "ok": true,  "svg"?: "<svg>", "type": "<diagram type>",
//             "width": n, "height": n }
//           { "ok": false, "error": "<message>", "code"?: "<pele error code>" }

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { pathToFileURL } from "node:url";

function reply(payload, exitCode) {
  process.stdout.write(JSON.stringify(payload));
  if (exitCode !== undefined) process.exitCode = exitCode;
}

function readStdin() {
  try {
    return readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

async function main() {
  let request;
  try {
    request = JSON.parse(readStdin() || "{}");
  } catch (error) {
    reply({ ok: false, error: `invalid request JSON: ${error.message}` }, 2);
    return;
  }

  const modulePath = request.module;
  if (!modulePath) {
    reply({ ok: false, error: "no pele module path provided" }, 2);
    return;
  }

  let pele;
  try {
    pele = await import(pathToFileURL(modulePath).href);
  } catch (error) {
    reply({ ok: false, error: `cannot load pele from '${modulePath}': ${error.message}` }, 3);
    return;
  }

  if (typeof pele.render !== "function") {
    reply({ ok: false, error: `'${modulePath}' does not export render()` }, 3);
    return;
  }

  let result;
  try {
    result = pele.render(String(request.source ?? ""), request.options ?? {});
  } catch (error) {
    reply(
      {
        ok: false,
        error: String((error && error.message) || error),
        code: (error && error.code) || "",
      },
      1,
    );
    return;
  }

  const svg = String(result.svg ?? "");
  if (request.outPath) {
    try {
      mkdirSync(dirname(request.outPath), { recursive: true });
      writeFileSync(request.outPath, svg, "utf8");
    } catch (error) {
      reply({ ok: false, error: `cannot write '${request.outPath}': ${error.message}` }, 4);
      return;
    }
  }

  reply({
    ok: true,
    svg: request.outPath ? undefined : svg,
    type: result.type ?? "",
    width: Number(result.width) || 0,
    height: Number(result.height) || 0,
  });
}

main();
