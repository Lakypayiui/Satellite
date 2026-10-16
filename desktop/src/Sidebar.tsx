/** Barra lateral: proyecto abierto, recientes y conversaciones del proyecto. */

import { useState } from "react";

import { SessionInfo } from "./api";

interface Props {
  contraido: boolean;
  proyectos: string[];
  actual: string;
  sesiones: SessionInfo[];
  activa: string;
  onToggle: () => void;
  onAbrir: (ruta?: string) => void;
  onNueva: () => void;
  onUsar: (id: string) => void;
  onRenombrar: (id: string, nombre: string) => void;
}

function nombreCorto(ruta: string): string {
  const partes = ruta.split(/[\\/]/).filter(Boolean);
  return partes.at(-1) ?? ruta;
}

export function Sidebar(props: Props) {
  const [renombrando, setRenombrando] = useState("");
  const [nombre, setNombre] = useState("");

  return (
    <aside className={props.contraido ? "sidebar contraido" : "sidebar"}>
      <div className="seccion">
        <h2>Proyecto</h2>
        <div className="title" title={props.actual}>
          {nombreCorto(props.actual) || "sin proyecto"}
        </div>
        <button style={{ marginTop: 8, width: "100%" }} onClick={() => props.onAbrir()}>
          Abrir carpeta…
        </button>
      </div>

      {props.proyectos.length > 1 && (
        <div className="seccion">
          <h2>Recientes</h2>
          {props.proyectos
            .filter((ruta) => ruta !== props.actual)
            .slice(0, 5)
            .map((ruta) => (
              <div key={ruta} className="fila" onClick={() => props.onAbrir(ruta)}>
                <span className="nombre" title={ruta}>
                  {nombreCorto(ruta)}
                </span>
                <span className="meta">{ruta.split(/[\\/]/).slice(0, -1).at(-1) ?? ""}</span>
              </div>
            ))}
        </div>
      )}

      <div className="seccion" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h2 style={{ margin: 0, flex: 1 }}>Conversaciones</h2>
        <button className="ghost" onClick={props.onNueva} title="Ctrl+N">
          + Nueva
        </button>
      </div>

      <div className="lista">
        {props.sesiones.length === 0 && (
          <div className="vacio">
            Sin conversaciones todavía.
            <br />
            Crea una y escribe lo que necesitas.
          </div>
        )}
        {props.sesiones.map((sesion) =>
          renombrando === sesion.id ? (
            <div key={sesion.id} className="fila">
              <input
                autoFocus
                value={nombre}
                onChange={(evento) => setNombre(evento.target.value)}
                onKeyDown={(evento) => {
                  if (evento.key === "Enter" && nombre.trim()) {
                    props.onRenombrar(sesion.id, nombre.trim());
                    setRenombrando("");
                  }
                  if (evento.key === "Escape") setRenombrando("");
                }}
              />
            </div>
          ) : (
            <div
              key={sesion.id}
              className={sesion.id === props.activa ? "fila activa" : "fila"}
              onClick={() => props.onUsar(sesion.id)}
              onDoubleClick={() => {
                setRenombrando(sesion.id);
                setNombre(sesion.name);
              }}
              title="Doble clic para renombrar"
            >
              <span className="nombre">{sesion.name || sesion.id}</span>
              <span className="meta">{sesion.turns}</span>
            </div>
          ),
        )}
      </div>

      <div className="pie">
        <span>doble clic: renombrar</span>
        <button className="ghost" onClick={props.onToggle} title="Ctrl+B">
          «
        </button>
      </div>
    </aside>
  );
}
