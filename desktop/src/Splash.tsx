/**
 * Splash de carga: fondo difuminado y el icono pequeño centrado. Se muestra mientras el
 * runtime del backend no está listo y desaparece al estarlo; si falla o se agota el tiempo,
 * la app continúa y enseña su banner normal (nada de quedarse tapando la interfaz).
 * El estilo usa variables del tema y respeta `prefers-reduced-motion`.
 */

import { Logo } from "./Logo";
import { useT } from "./i18n";

export function Splash() {
  const t = useT();
  return (
    <div className="splash" role="status" aria-live="polite" aria-label={t("app.loading")}>
      <div className="splash-caja">
        <Logo className="splash-logo" title="" />
        <span className="splash-texto">{t("app.loading")}</span>
      </div>
    </div>
  );
}
