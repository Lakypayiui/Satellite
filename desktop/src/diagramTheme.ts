/**
 * Theme-aware presentation of rendered diagrams.
 *
 * The SVG written by Pele is deliberately left untouched (it keeps its white/light
 * defaults on disk). The chat displays a *copy* whose Pele CSS variables are set to
 * the current appearance colors, so the diagram background and nodes match the app
 * theme (dark, light, deep dark, …).
 */

export interface DiagramColors {
  bg: string;
  text: string;
  muted: string;
  line: string;
  surface: string;
  surfaceAlt: string;
  border: string;
  accent: string;
}

const FALLBACKS: DiagramColors = {
  bg: "#ffffff",
  text: "#1f1f1f",
  muted: "#6e6e6e",
  line: "#1f1f1f",
  surface: "#f3f3f3",
  surfaceAlt: "#e6e6e6",
  border: "#8a8a8a",
  accent: "#5b7bd5",
};

function cssValue(style: CSSStyleDeclaration, name: string, fallback: string): string {
  return style.getPropertyValue(name).trim() || fallback;
}

/** Read the palette that the active theme exposes in the document. */
export function readDiagramColors(root: HTMLElement): DiagramColors {
  const style = getComputedStyle(root);
  return {
    bg: cssValue(style, "--bg", FALLBACKS.bg),
    text: cssValue(style, "--text", FALLBACKS.text),
    muted: cssValue(style, "--muted", FALLBACKS.muted),
    // Edges/arrows need more contrast than a separator line, especially in dark themes.
    line: cssValue(style, "--text-soft", FALLBACKS.line),
    surface: cssValue(style, "--bg-2", FALLBACKS.surface),
    surfaceAlt: cssValue(style, "--bg-3", FALLBACKS.surfaceAlt),
    border: cssValue(style, "--line", FALLBACKS.border),
    accent: cssValue(style, "--accent", FALLBACKS.accent),
  };
}

/**
 * Return a copy of `svg` with Pele's CSS variables set from `colors`.
 *
 * Pele emits inline variables such as `--_bg: var(--pele-bg, #fff)`. Defining the
 * `--pele-*` variables on the root makes every node/edge/label use the theme palette
 * without rewriting the SVG structure. The original string is never mutated.
 */
export function applyDiagramTheme(svg: string, colors: DiagramColors): string {
  if (!svg) return svg;
  const declarations = [
    `--pele-bg:${colors.bg}`,
    `--pele-fg:${colors.text}`,
    `--pele-muted:${colors.muted}`,
    `--pele-line:${colors.line}`,
    `--pele-surface:${colors.surface}`,
    `--pele-surface-alt:${colors.surfaceAlt}`,
    `--pele-border:${colors.border}`,
    `--pele-accent:${colors.accent}`,
  ].join(";");

  return svg.replace(/<svg\b[^>]*>/i, (tag) => {
    if (/\bstyle\s*=\s*"/i.test(tag)) {
      return tag.replace(
        /\bstyle\s*=\s*"([^"]*)"/i,
        (_match, value: string) => `style="${value.replace(/;\s*$/, "")};${declarations}"`,
      );
    }
    return tag.replace(/>$/, ` style="${declarations}">`);
  });
}
