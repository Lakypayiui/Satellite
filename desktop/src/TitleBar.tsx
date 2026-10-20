import { useEffect, useState } from "react";

import { bridge } from "./api";
import { useT } from "./i18n";
import { Logo } from "./Logo";

/**
 * Franja de título propia: la ventana va con `frame:false`, así que el chrome nativo (y el
 * blanco que arrastraba) desaparece. Arrastra la ventana y ofrece minimizar, maximizar/restaurar
 * y cerrar por IPC validado. Sigue el tema activo porque usa las variables `--titlebar-*`.
 *
 * En macOS se usan los semáforos NATIVOS (rojo/amarillo/verde) que dibuja el sistema con
 * `titleBarStyle: hiddenInset`; aquí sólo se reserva su espacio. En Windows y Linux los botones
 * propios van a la derecha y son `<button>` normales: tabulables y operables con teclado.
 * Los rótulos visibles (títulos y aria) salen del i18n tipado (`useT`).
 */
export function TitleBar({ proyecto }: { proyecto: string }) {
  const t = useT();
  const [maximizado, setMaximizado] = useState(false);
  const [plataforma, setPlataforma] = useState("win32");

  useEffect(() => {
    let vigente = true;
    setPlataforma(bridge.plataforma() || "win32");
    void bridge
      .estadoVentana()
      .then((estado) => {
        if (vigente) setMaximizado(estado.maximized);
      })
      .catch(() => {});
    const cortar = bridge.alCambiarVentana((estado) => setMaximizado(estado.maximized));
    return () => {
      vigente = false;
      cortar();
    };
  }, []);

  const esMac = plataforma === "darwin";

  const control = (clave: "minimizar" | "maximizar" | "cerrar") => {
    if (esMac) return null;
    if (clave === "minimizar") {
      return (
        <button
          key="minimizar"
          type="button"
          className="barra-control"
          onClick={() => bridge.minimizar()}
          aria-label={t("titlebar.minimize")}
          title={t("titlebar.minimize")}
        >
          <span aria-hidden="true">–</span>
        </button>
      );
    }
    if (clave === "maximizar") {
      return (
        <button
          key="maximizar"
          type="button"
          className="barra-control"
          onClick={() => bridge.alternarMaximizar()}
          aria-label={maximizado ? t("titlebar.restore") : t("titlebar.maximize")}
          title={maximizado ? t("titlebar.restore") : t("titlebar.maximize")}
        >
          <span aria-hidden="true">{maximizado ? "❐" : "□"}</span>
        </button>
      );
    }
    return (
      <button
        key="cerrar"
        type="button"
        className="barra-control cerrar"
        onClick={() => bridge.cerrar()}
        aria-label={t("titlebar.close")}
        title={t("titlebar.close")}
      >
        <span aria-hidden="true">✕</span>
      </button>
    );
  };

  const orden = ["minimizar", "maximizar", "cerrar"] as const;

  const controles = (
    <div className="barra-controles" role="group" aria-label={t("titlebar.controls")}>
      {orden.map((clave) => control(clave))}
    </div>
  );

  return (
    <header
      className="barra-titulo"
      aria-label={t("titlebar.bar")}
      data-mac={esMac ? "true" : undefined}
    >
      {esMac && <span className="barra-hueco-mac" aria-hidden="true" />}
      <div className="barra-marca">
        <Logo className="barra-logo" />
        <span className="barra-app">Satellite</span>
        {proyecto && <span className="barra-proyecto">· {proyecto}</span>}
      </div>
      <div className="barra-espaciador" />
      {!esMac && controles}
    </header>
  );
}
