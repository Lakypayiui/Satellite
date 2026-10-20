import { useEffect, useState } from "react";

import { readDiagramColors, type DiagramColors } from "./diagramTheme";

/**
 * Current appearance palette, kept in sync with `data-theme`/`data-intensidad`.
 * A MutationObserver is needed because the theme can change without a React render
 * in this subtree (App writes the attributes on <html>).
 */
export function useDiagramColors(): DiagramColors | null {
  const [colors, setColors] = useState<DiagramColors | null>(() => {
    if (typeof document === "undefined") return null;
    return readDiagramColors(document.documentElement);
  });

  useEffect(() => {
    const update = () => setColors(readDiagramColors(document.documentElement));
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-intensidad"],
    });
    return () => observer.disconnect();
  }, []);

  return colors;
}
