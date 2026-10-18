import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import {
  DEFAULT_CHAT_WATERMARK_OPACITY,
  DEFAULT_TEXT_INTENSITY,
  DEFAULT_THEME,
  clampWatermarkOpacity,
  isTextIntensity,
  isThemeId,
} from "../electron/themes";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import "./styles.css";

// Se aplican tema e intensidad persistidos antes de renderizar (los pasa el proceso principal
// por `additionalArguments`), para que la titlebar y el resto arranquen ya con el aspecto elegido.
const temaInicial: unknown = window.satellite?.theme?.initial ?? DEFAULT_THEME;
document.documentElement.dataset.theme = isThemeId(temaInicial) ? temaInicial : DEFAULT_THEME;

const intensidadInicial: unknown = window.satellite?.theme?.initialIntensity ?? DEFAULT_TEXT_INTENSITY;
document.documentElement.dataset.intensidad = isTextIntensity(intensidadInicial)
  ? intensidadInicial
  : DEFAULT_TEXT_INTENSITY;

// Opacidad de la marca de agua del chat: se aplica antes de pintar para no parpadear.
const opacidadInicial: unknown =
  window.satellite?.theme?.initialWatermarkOpacity ?? DEFAULT_CHAT_WATERMARK_OPACITY;
document.documentElement.style.setProperty(
  "--chat-marca-opacidad",
  String(clampWatermarkOpacity(opacidadInicial)),
);

const contenedor = document.getElementById("root");
if (contenedor) {
  createRoot(contenedor).render(
    <StrictMode>
      <I18nProvider>
        <App />
      </I18nProvider>
    </StrictMode>,
  );
}
