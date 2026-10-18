/**
 * Genera los formatos de icono del empaquetado **a partir del arte original** de
 * `Icono/` (no se dibuja nada nuevo ni se retoca el original): centra al cuadrado,
 * escala y escribe `build/icon.png` (512), `build/icon.ico` (Windows, multinivel) y
 * `build/icon.icns` (macOS), más las variantes `-light`.
 *
 * Se ejecuta con **Node puro** (`npm run icons` → `node scripts/icons.mjs`), sin
 * Electron: así funciona *headless* en cualquier sitio, incluido el runner de Linux de
 * CI, donde no hay `node_modules` ni servidor X. (Con Electron habría que arrancar la
 * app y, en Linux, envolver todo en `xvfb-run`: dependencia de sistema que aquí no
 * hace falta y que rompía un checkout limpio.) El decodificado/codificado PNG usa sólo
 * `node:zlib`; el redimensionado es un promedio por área (buena calidad al reducir).
 *
 * Entradas: `Icono/Ico_white` (arte para tema oscuro) e `Icono/Ico_Black` (tema claro).
 * Salidas: `desktop/build/` (ignorado por git: son artefactos de empaquetado).
 */

import { deflateSync, inflateSync } from "node:zlib";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const salida = path.resolve(here, "..", "build");

const FIRMA_PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

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

// --------------------------------------------------------------------------- //
// PNG: leer (zlib + desfiltrado), reducir y escribir
// --------------------------------------------------------------------------- //
const CANALES = { 0: 1, 2: 3, 4: 2, 6: 4 };

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

function desfiltrar(tipo, linea, previa, bpp) {
  const n = linea.length;
  switch (tipo) {
    case 0:
      break;
    case 1:
      for (let i = bpp; i < n; i++) linea[i] = (linea[i] + linea[i - bpp]) & 255;
      break;
    case 2:
      for (let i = 0; i < n; i++) linea[i] = (linea[i] + previa[i]) & 255;
      break;
    case 3:
      for (let i = 0; i < n; i++) {
        const a = i >= bpp ? linea[i - bpp] : 0;
        linea[i] = (linea[i] + ((a + previa[i]) >> 1)) & 255;
      }
      break;
    case 4:
      for (let i = 0; i < n; i++) {
        const a = i >= bpp ? linea[i - bpp] : 0;
        const c = i >= bpp ? previa[i - bpp] : 0;
        linea[i] = (linea[i] + paeth(a, previa[i], c)) & 255;
      }
      break;
    default:
      throw new Error(`filtro PNG desconocido: ${tipo}`);
  }
}

/** Lee un PNG (8 bits, sin entrelazar) y devuelve RGBA. */
function leerPng(ruta) {
  const buf = readFileSync(ruta);
  if (buf.length < 8 || !buf.subarray(0, 8).equals(FIRMA_PNG)) {
    throw new Error(`no es un PNG: ${ruta}`);
  }
  let pos = 8;
  let width = 0;
  let height = 0;
  let color = 0;
  const idat = [];
  while (pos + 8 <= buf.length) {
    const largo = buf.readUInt32BE(pos);
    const tipo = buf.toString("ascii", pos + 4, pos + 8);
    const datos = buf.subarray(pos + 8, pos + 8 + largo);
    if (tipo === "IHDR") {
      width = datos.readUInt32BE(0);
      height = datos.readUInt32BE(4);
      if (datos[8] !== 8) throw new Error(`sólo se admite PNG de 8 bits: ${ruta}`);
      if (datos[12] !== 0) throw new Error(`PNG entrelazado no soportado: ${ruta}`);
      color = datos[9];
      if (!(color in CANALES)) throw new Error(`color PNG no soportado (${color}): ${ruta}`);
    } else if (tipo === "IDAT") {
      idat.push(datos);
    } else if (tipo === "IEND") {
      break;
    }
    pos += 12 + largo;
  }
  if (!width || !height) throw new Error(`PNG sin IHDR válido: ${ruta}`);

  const canales = CANALES[color];
  const ancho = width * canales;
  const crudo = inflateSync(Buffer.concat(idat));
  const rgba = Buffer.alloc(width * height * 4);
  let previa = Buffer.alloc(ancho);
  let off = 0;
  for (let y = 0; y < height; y++) {
    const filtro = crudo[off++];
    const linea = Buffer.from(crudo.subarray(off, off + ancho));
    off += ancho;
    desfiltrar(filtro, linea, previa, canales);
    for (let x = 0; x < width; x++) {
      const s = x * canales;
      const d = (y * width + x) * 4;
      if (canales === 1) {
        rgba[d] = rgba[d + 1] = rgba[d + 2] = linea[s];
        rgba[d + 3] = 255;
      } else if (canales === 2) {
        rgba[d] = rgba[d + 1] = rgba[d + 2] = linea[s];
        rgba[d + 3] = linea[s + 1];
      } else if (canales === 3) {
        rgba[d] = linea[s];
        rgba[d + 1] = linea[s + 1];
        rgba[d + 2] = linea[s + 2];
        rgba[d + 3] = 255;
      } else {
        rgba[d] = linea[s];
        rgba[d + 1] = linea[s + 1];
        rgba[d + 2] = linea[s + 2];
        rgba[d + 3] = linea[s + 3];
      }
    }
    previa = linea;
  }
  return { width, height, rgba };
}

let TABLA_CRC = null;

function crc32(buf) {
  if (TABLA_CRC === null) {
    TABLA_CRC = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      TABLA_CRC[n] = c;
    }
  }
  let c = -1;
  for (let i = 0; i < buf.length; i++) c = TABLA_CRC[(c ^ buf[i]) & 255] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function trozo(tipo, datos) {
  const cabecera = Buffer.alloc(8);
  cabecera.writeUInt32BE(datos.length, 0);
  cabecera.write(tipo, 4, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([Buffer.from(tipo, "ascii"), datos])), 0);
  return Buffer.concat([cabecera, datos, crc]);
}

/** Escribe un PNG RGBA cuadrado. */
function escribirPng(rgba, lado) {
  const ancho = lado * 4;
  const crudo = Buffer.alloc((ancho + 1) * lado);
  for (let y = 0; y < lado; y++) {
    crudo[y * (ancho + 1)] = 0; // filtro "none": lo comprime zlib igual de bien
    rgba.copy(crudo, y * (ancho + 1) + 1, y * ancho, (y + 1) * ancho);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(lado, 0);
  ihdr.writeUInt32BE(lado, 4);
  ihdr[8] = 8; // profundidad
  ihdr[9] = 6; // RGBA
  return Buffer.concat([
    FIRMA_PNG,
    trozo("IHDR", ihdr),
    trozo("IDAT", deflateSync(crudo, { level: 9 })),
    trozo("IEND", Buffer.alloc(0)),
  ]);
}

/**
 * Recorta al cuadrado centrado y reduce a `lado` (promedio por área).
 *
 * El color se promedia **premultiplicado por alfa** y luego se des-premultiplica: si se
 * promediara el RGB directo, los píxeles totalmente transparentes (que pueden llevar
 * cualquier color) se colarían y dejarían halos en los bordes del icono. La media de
 * alfa sí es la del área.
 */
function reducir(imagen, lado) {
  const { width, height, rgba } = imagen;
  const base = Math.min(width, height);
  const ox = Math.floor((width - base) / 2);
  const oy = Math.floor((height - base) / 2);
  const out = Buffer.alloc(lado * lado * 4);
  for (let y = 0; y < lado; y++) {
    const y0 = oy + Math.floor((y * base) / lado);
    const y1 = oy + Math.max(Math.floor(((y + 1) * base) / lado), Math.floor((y * base) / lado) + 1);
    for (let x = 0; x < lado; x++) {
      const x0 = ox + Math.floor((x * base) / lado);
      const x1 = ox + Math.max(Math.floor(((x + 1) * base) / lado), Math.floor((x * base) / lado) + 1);
      let pr = 0;
      let pg = 0;
      let pb = 0;
      let sumaAlfa = 0;
      let n = 0;
      for (let yy = y0; yy < y1; yy++) {
        for (let xx = x0; xx < x1; xx++) {
          const s = (yy * width + xx) * 4;
          const alfa = rgba[s + 3];
          pr += rgba[s] * alfa;
          pg += rgba[s + 1] * alfa;
          pb += rgba[s + 2] * alfa;
          sumaAlfa += alfa;
          n++;
        }
      }
      const d = (y * lado + x) * 4;
      if (sumaAlfa > 0) {
        out[d] = Math.round(pr / sumaAlfa);
        out[d + 1] = Math.round(pg / sumaAlfa);
        out[d + 2] = Math.round(pb / sumaAlfa);
      }
      out[d + 3] = Math.round(sumaAlfa / n);
    }
  }
  return out;
}

const TAMANOS_ICO = [256, 128, 64, 48, 32, 16];
const TAMANOS_ICNS = [
  ["icp4", 16],
  ["icp5", 32],
  ["icp6", 64],
  ["ic07", 128],
  ["ic08", 256],
  ["ic09", 512],
  ["ic10", 1024],
];

function escribir(imagen, sufijo) {
  const cache = new Map();
  const png = (size) => {
    if (!cache.has(size)) cache.set(size, escribirPng(reducir(imagen, size), size));
    return cache.get(size);
  };
  writeFileSync(path.join(salida, `icon${sufijo}.png`), png(512));
  writeFileSync(
    path.join(salida, `icon${sufijo}.ico`),
    buildIco(TAMANOS_ICO.map((size) => ({ size, png: png(size) }))),
  );
  writeFileSync(
    path.join(salida, `icon${sufijo}.icns`),
    buildIcns(TAMANOS_ICNS.map(([type, size]) => ({ type, png: png(size) }))),
  );
}

function main() {
  mkdirSync(salida, { recursive: true });
  escribir(leerPng(path.join(repo, "Icono", "Ico_white")), "");
  escribir(leerPng(path.join(repo, "Icono", "Ico_Black")), "-light");
  console.log(`iconos generados en ${salida}`);
}

try {
  main();
} catch (exc) {
  console.error(String(exc && exc.message ? exc.message : exc));
  process.exit(1);
}
