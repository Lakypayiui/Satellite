/**
 * Catálogo de temas de Satellite Desktop.
 *
 * Vive en `electron/` porque lo usan **las dos partes**: el proceso principal (fondo nativo de la
 * ventana antes de pintar y validación del tema persistido) y el renderer (página Apariencia y
 * atributo `data-theme`). Los colores completos de cada tema están en `src/styles.css`; aquí van
 * la identidad, el fondo de ventana y los swatches de la vista previa.
 */

export type ThemeId = "obsidiana" | "grafito" | "medianoche" | "bosque" | "claro" | "deepdark";

/** Niveles de intensidad del blanco; sólo Deep Dark los usa. */
export type TextIntensityId = "suave" | "medio" | "brillante" | "maximo";

export interface TextIntensityDef {
  id: TextIntensityId;
  label: string;
  hint: string;
}

/** Progresión de gris claro a blanco puro para el texto de Deep Dark. */
export const TEXT_INTENSITIES: readonly TextIntensityDef[] = [
  { id: "suave", label: "Suave", hint: "gris claro" },
  { id: "medio", label: "Medio", hint: "blanco tenue" },
  { id: "brillante", label: "Brillante", hint: "casi blanco puro" },
  { id: "maximo", label: "Máximo", hint: "blanco puro" },
];

/** Intensidad por defecto (compatible con preferencias previas sin el campo). */
export const DEFAULT_TEXT_INTENSITY: TextIntensityId = "medio";

/** Marca de agua del chat: visible por defecto, con opacidad elegida por la persona. */
export const DEFAULT_CHAT_WATERMARK = true;
export const DEFAULT_CHAT_WATERMARK_OPACITY = 0.06;
/** Límites pensados para notarse sin estorbar la lectura. */
export const CHAT_WATERMARK_OPACITY_MIN = 0.01;
export const CHAT_WATERMARK_OPACITY_MAX = 0.2;
export const CHAT_WATERMARK_OPACITY_STEP = 0.01;

/** Normaliza la opacidad a un valor válido del rango (tolerante con datos previos). */
export function clampWatermarkOpacity(value: unknown): number {
  const numero = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numero)) return DEFAULT_CHAT_WATERMARK_OPACITY;
  const redondeado =
    Math.round(numero / CHAT_WATERMARK_OPACITY_STEP) * CHAT_WATERMARK_OPACITY_STEP;
  const acotado = Math.min(
    CHAT_WATERMARK_OPACITY_MAX,
    Math.max(CHAT_WATERMARK_OPACITY_MIN, redondeado),
  );
  return Number(acotado.toFixed(2));
}

/** Idioma de la interfaz (no cambia formatos ni zonas horarias). */
export type IdiomaId = "es" | "en";

export interface IdiomaDef {
  id: IdiomaId;
  label: string;
}

export const IDIOMAS: readonly IdiomaDef[] = [
  { id: "es", label: "Español" },
  { id: "en", label: "English" },
];

/** Español para instalaciones sin preferencia guardada. */
export const DEFAULT_IDIOMA: IdiomaId = "es";

const IDIOMAS_VALIDOS = new Set<string>(IDIOMAS.map((idioma) => idioma.id));

export function isIdioma(value: unknown): value is IdiomaId {
  return typeof value === "string" && IDIOMAS_VALIDOS.has(value);
}

export interface ThemeSwatches {
  /** Fondo principal del tema. */
  background: string;
  /** Fondo de paneles y tarjetas. */
  panel: string;
  /** Texto principal. */
  text: string;
  /** Acento. */
  accent: string;
}

export interface ThemeDef {
  id: ThemeId;
  name: string;
  description: string;
  /** Fondo de la ventana Electron = `--bg` del tema. */
  background: string;
  /** `true` para temas claros (`color-scheme: light`). */
  light: boolean;
  swatches: ThemeSwatches;
}

export const THEMES: readonly ThemeDef[] = [
  {
    id: "obsidiana",
    name: "Obsidiana",
    description: "Oscuro cálido, acento verde. El aspecto de siempre.",
    background: "#1c1a19",
    light: false,
    swatches: { background: "#1c1a19", panel: "#201e1d", text: "#f2efec", accent: "#8fbf9f" },
  },
  {
    id: "grafito",
    name: "Grafito",
    description: "Gris neutro frío con acento acero.",
    background: "#202325",
    light: false,
    swatches: { background: "#202325", panel: "#26292c", text: "#eef1f3", accent: "#9fb8cc" },
  },
  {
    id: "medianoche",
    name: "Medianoche",
    description: "Azul profundo con acento eléctrico.",
    background: "#0f1724",
    light: false,
    swatches: { background: "#0f1724", panel: "#141f30", text: "#e8eef8", accent: "#6fa8ff" },
  },
  {
    id: "bosque",
    name: "Bosque",
    description: "Verde profundo y acento vegetal.",
    background: "#121b16",
    light: false,
    swatches: { background: "#121b16", panel: "#17231c", text: "#e8f2e9", accent: "#9fd39a" },
  },
  {
    id: "claro",
    name: "Claro cálido",
    description: "Papel cálido con tinta oscura y acento verde.",
    background: "#f4eee5",
    light: true,
    swatches: { background: "#f4eee5", panel: "#ece4d8", text: "#2b2621", accent: "#2f7d5c" },
  },
  {
    id: "deepdark",
    name: "Deep Dark",
    description: "Negro puro en toda la app, con blanco ajustable.",
    background: "#000000",
    light: false,
    swatches: { background: "#000000", panel: "#141414", text: "#ededed", accent: "#6fd3ff" },
  },
];

/** Tema por defecto: el aspecto oscuro original. Sin migración destructiva. */
export const DEFAULT_THEME: ThemeId = "obsidiana";

const IDS = new Set<string>(THEMES.map((tema) => tema.id));
const INTENSIDADES = new Set<string>(TEXT_INTENSITIES.map((nivel) => nivel.id));

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === "string" && IDS.has(value);
}

export function isTextIntensity(value: unknown): value is TextIntensityId {
  return typeof value === "string" && INTENSIDADES.has(value);
}

export function themeById(value: unknown): ThemeDef {
  return THEMES.find((tema) => tema.id === value) ?? THEMES[0];
}
