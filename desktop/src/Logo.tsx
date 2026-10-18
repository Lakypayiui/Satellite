/**
 * Icono de Satellite desde la carpeta de iconos del repo (copias sin retocar del arte
 * original). Se sirven dos variantes y el CSS muestra la que corresponde al tema: el arte
 * blanco para los temas oscuros y el negro para el claro, sin invertir ni recolorear nada.
 */

import negro from "./assets/satellite-black.png";
import blanco from "./assets/satellite-white.png";

interface Props {
  /** Clase extra para dar tamaño/posición (p. ej. barra, marca de agua, splash). */
  className?: string;
  /** Texto alternativo; vacío para uso decorativo (marca de agua). */
  title?: string;
}

export function Logo({ className = "", title = "Satellite" }: Props) {
  const clase = `logo ${className}`.trim();
  return (
    <>
      <img
        className={`${clase} logo-oscuro`}
        src={blanco}
        alt={title}
        aria-hidden={title ? undefined : true}
        draggable={false}
      />
      <img
        className={`${clase} logo-claro`}
        src={negro}
        alt={title}
        aria-hidden={title ? undefined : true}
        draggable={false}
      />
    </>
  );
}
