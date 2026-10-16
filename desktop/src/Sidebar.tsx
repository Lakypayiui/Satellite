/** Barra lateral: proyecto abierto, conversaciones y recientes. Sin adornos. */

import { useState } from "react";

import { SavedProviderView, SessionInfo } from "./api";

interface Props {
  plegado: boolean;
  ruta: string;
  recientes: string[];
  sesiones: SessionInfo[];
  activa: string;
  proveedor: SavedProviderView | null;
  onPlegar: () => void;
  onAbrir: (ruta?: string) => void;
  onNueva: () => void;
  onUsar: (id: string) => void;
  onRenombrar: (id: string, nombre: string) => void;
}

function corto(ruta: string): string {
  return ruta.split(/[\\/]/).filter(Boolean).at(-1) ?? ruta;
}

export function Sidebar(props: Props) {
  const [renombrando, setRenombrando] = useState("");
  const [nombre, setNombre] = useState("");

  if (props.plegado) {
    return (
      <aside className="panel izq plegado">
        <button className="plegar" onClick={props.onPlegar} title="Ctrl+B">
          ›
        </button>
      </aside>
    );
  }

  return (
    <aside className="panel izq">
      <button className="plegar" onClick={props.onPlegar} title="Ctrl+B">
        ‹
      </button>

      <div className="bloque">
        <p className="etiqueta">Proyecto</p>
        <div className="item" style={{ padding: 0, cursor: "default" }}>
          <span className="nombre" title={props.ruta}>
            {corto(props.ruta) || "sin proyecto"}
          </span>
        </div>
        <div className="evento" style={{ padding: "2px 0" }}>
          <span>{props.ruta || "—"}</span>
        </div>
        <div className="acciones">
          <button className="quiet" onClick={() => props.onAbrir()}>
            cambiar carpeta…
          </button>
        </div>
      </div>

      <div className="bloque" style={{ display: "flex", alignItems: "baseline" }}>
        <p className="etiqueta" style={{ flex: 1, margin: 0 }}>
          Conversaciones
        </p>
        <button className="quiet" onClick={props.onNueva} title="Ctrl+N">
          nueva
        </button>
      </div>

      <div className="lista">
        {props.sesiones.length === 0 && (
          <div className="vacio">
            Sin conversaciones.
            <br />
            Crea una y escribe el objetivo.
          </div>
        )}
        {props.sesiones.map((sesion) =>
          renombrando === sesion.id ? (
            <div key={sesion.id} className="item">
              <input
                autoFocus
                value={nombre}
                onChange={(evento) => setNombre(evento.target.value)}
                onBlur={() => setRenombrando("")}
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
              className={sesion.id === props.activa ? "item activo" : "item"}
              onClick={() => props.onUsar(sesion.id)}
              onDoubleClick={() => {
                setRenombrando(sesion.id);
                setNombre(sesion.name);
              }}
              title="doble clic para renombrar"
            >
              <span className="nombre">{sesion.name || sesion.id}</span>
              <span className="meta">{sesion.turns}</span>
            </div>
          ),
        )}
      </div>

      {props.recientes.filter((ruta) => ruta !== props.ruta).length > 0 && (
        <div className="bloque">
          <p className="etiqueta">Recientes</p>
          {props.recientes
            .filter((ruta) => ruta !== props.ruta)
            .slice(0, 4)
            .map((ruta) => (
              <div key={ruta} className="item" onClick={() => props.onAbrir(ruta)} title={ruta}>
                <span className="nombre">{corto(ruta)}</span>
              </div>
            ))}
        </div>
      )}

      <div className="bloque" style={{ marginTop: "auto" }}>
        <div className="evento" style={{ padding: 0 }}>
          <span>{props.proveedor ? props.proveedor.name : "sin proveedor"}</span>
          {props.proveedor?.api_key_set === false && <span className="tipo">sin clave</span>}
        </div>
      </div>
    </aside>
  );
}
