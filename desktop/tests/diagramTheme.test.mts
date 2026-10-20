import assert from "node:assert/strict";
import test from "node:test";

import { applyDiagramTheme, type DiagramColors } from "../src/diagramTheme.ts";

const COLORS: DiagramColors = {
  bg: "#0f1724",
  text: "#e8eef8",
  muted: "#7c8ea8",
  line: "#b9c8de",
  surface: "#162033",
  surfaceAlt: "#0b1220",
  border: "#22314a",
  accent: "#6fa8ff",
};

test("sets Pele theme variables on the root SVG", () => {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" style="--_bg:var(--pele-bg,#fff);--_fg:var(--pele-fg,#1f1f1f)"><rect fill="var(--_bg)"/></svg>';
  const themed = applyDiagramTheme(svg, COLORS);

  assert.match(themed, /--pele-bg:#0f1724/);
  assert.match(themed, /--pele-fg:#e8eef8/);
  assert.match(themed, /--pele-surface:#162033/);
  assert.match(themed, /--pele-line:#b9c8de/);
  // The Pele references and their light fallbacks stay intact in the string.
  assert.match(themed, /var\(--pele-bg,#fff\)/);
});

test("adds a style attribute when the SVG has none", () => {
  const themed = applyDiagramTheme("<svg><g/></svg>", COLORS);
  assert.match(themed, /^<svg style="--pele-bg:#0f1724;/);
});

test("leaves empty input unchanged", () => {
  assert.equal(applyDiagramTheme("", COLORS), "");
});
