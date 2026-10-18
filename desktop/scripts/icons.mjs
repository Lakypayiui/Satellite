/**
 * Genera los formatos de icono del empaquetado **a partir del arte original** de
 * `Icono/` (no se dibuja nada nuevo ni se retoca el original): centra al cuadrado,
 * escala y escribe `build/icon.png` (512), `build/icon.ico` (Windows, multinivel) y
 * `build/icon.icns` (macOS), más las variantes `-light`.
 *
 * Se ejecuta con Electron (ya es dependencia de desarrollo): `npm run icons`.
 */

import { app, nativeImage } from "electron";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const salida = path.resolve(here, "..", "build");

/** ICO con entradas PNG (Windows Vista+). */
function buildIco(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(entries.length, 4);
  const dir = Buffer.alloc(16 * entries.length);
  let offset = 6 + dir.length;
  const datos = [];
  entries.forEach((entrada, indice) => {
    const base = indice * 16;
    dir.writeUInt8(entrada.size >= 256 ? 0 : entrada.size, base);
    dir.writeUInt8(entrada.size >= 256 ? 0 : entrada.size, base + 1);
    dir.writeUInt8(0, base + 2);
    dir.writeUInt8(0, base + 3);
    dir.writeUInt16LE(1, base + 4);
    dir.writeUInt16LE(32, base + 6);
    dir.writeUInt32LE(entrada.png.length, base + 8);
    dir.writeUInt32LE(offset, base + 12);
    offset += entrada.png.length;
    datos.push(entrada.png);
  });
  return Buffer.concat([header, dir, ...datos]);
}

/** ICNS con entradas PNG (macOS). */
function buildIcns(entries) {
  const trozos = entries.map((entrada) => {
    const cabecera = Buffer.alloc(8);
    cabecera.write(entrada.type, 0, 4, "ascii");
    cabecera.writeUInt32BE(entrada.png.length + 8, 4);
    return Buffer.concat([cabecera, entrada.png]);
  });
  const cuerpo = Buffer.concat(trozos);
  const cabecera = Buffer.alloc(8);
  cabecera.write("icns", 0, 4, "ascii");
  cabecera.writeUInt32BE(cuerpo.length + 8, 4);
  return Buffer.concat([cabecera, cuerpo]);
}

function cuadrada(ruta) {
  const base = nativeImage.createFromPath(ruta);
  if (base.isEmpty()) throw new Error(`no se pudo leer el icono: ${ruta}`);
  const { width, height } = base.getSize();
  const lado = Math.min(width, height);
  return base.crop({
    x: Math.floor((width - lado) / 2),
    y: Math.floor((height - lado) / 2),
    width: lado,
    height: lado,
  });
}

function escribir(base, sufijo) {
  const png = (size) =>
    base.resize({ width: size, height: size, quality: "best" }).toPNG();
  writeFileSync(path.join(salida, `icon${sufijo}.png`), png(512));
  writeFileSync(
    path.join(salida, `icon${sufijo}.ico`),
    buildIco([256, 128, 64, 48, 32, 16].map((size) => ({ size, png: png(size) }))),
  );
  writeFileSync(
    path.join(salida, `icon${sufijo}.icns`),
    buildIcns(
      [
        ["icp4", 16],
        ["icp5", 32],
        ["icp6", 64],
        ["ic07", 128],
        ["ic08", 256],
        ["ic09", 512],
        ["ic10", 1024],
      ].map(([type, size]) => ({ type, png: png(size) })),
    ),
  );
}

async function main() {
  mkdirSync(salida, { recursive: true });
  escribir(cuadrada(path.join(repo, "Icono", "Ico_white")), "");
  escribir(cuadrada(path.join(repo, "Icono", "Ico_Black")), "-light");
  console.log(`iconos generados en ${salida}`);
}

app
  .whenReady()
  .then(main)
  .then(() => app.quit())
  .catch((exc) => {
    console.error(String(exc));
    app.exit(1);
  });
