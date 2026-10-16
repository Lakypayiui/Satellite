/** Ajustes: proveedor, política de seguridad y aviso honesto del sandbox. */

import { useState } from "react";

import { ConfigView } from "./api";

interface Props {
  config: ConfigView;
  onCerrar: () => void;
  onPolitica: (modo: string) => void;
  onModo: (activo: boolean) => void;
  onProveedor: (datos: {
    name: string;
    model: string;
    base_url: string;
    api_key: string;
    api_key_env: string;
  }) => void;
}

const MODOS: { valor: string; texto: string }[] = [
  { valor: "ask", texto: "ask — pregunta cada permiso" },
  { valor: "auto", texto: "auto — aprueba lo de bajo riesgo" },
  { valor: "unrestricted", texto: "unrestricted — no pregunta nada" },
];

export function Settings(props: Props) {
  const [nombre, setNombre] = useState("");
  const [modelo, setModelo] = useState("");
  const [url, setUrl] = useState("");
  const [clave, setClave] = useState("");
  const [variable, setVariable] = useState("");

  return (
    <div className="modal">
      <div className="caja">
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1, margin: 0 }}>Ajustes</h2>
          <button className="ghost" onClick={props.onCerrar}>
            Cerrar
          </button>
        </div>

        <div className="seccion">
          <h2>Proveedor</h2>
          <div className="rejilla">
            <div className="campo">
              <label>Nombre</label>
              <input value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="openai" />
            </div>
            <div className="campo">
              <label>Modelo</label>
              <input value={modelo} onChange={(e) => setModelo(e.target.value)} placeholder="gpt-4o-mini" />
            </div>
          </div>
          <div className="campo">
            <label>URL base</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
          </div>
          <div className="rejilla">
            <div className="campo">
              <label>Variable de entorno con la clave (recomendado)</label>
              <input value={variable} onChange={(e) => setVariable(e.target.value)} placeholder="OPENAI_API_KEY" />
            </div>
            <div className="campo">
              <label>O la clave (no se vuelve a mostrar nunca)</label>
              <input
                type="password"
                value={clave}
                onChange={(e) => setClave(e.target.value)}
                placeholder="sk-…"
              />
            </div>
          </div>
          <button
            className="primary"
            disabled={!nombre.trim() || !modelo.trim() || !url.trim()}
            onClick={() => {
              props.onProveedor({
                name: nombre.trim(),
                model: modelo.trim(),
                base_url: url.trim(),
                api_key: clave,
                api_key_env: variable.trim(),
              });
              setClave("");
            }}
          >
            Guardar y activar
          </button>
          <div className="sub" style={{ marginTop: 8 }}>
            Guardados: {props.config.providers.length} · activo: {props.config.active_provider || "ninguno"} ·
            proveedor y modelo se comparten con el CLI y la TUI (mismo archivo de configuración).
          </div>
        </div>

        <div className="seccion">
          <h2>Política de seguridad</h2>
          {MODOS.map((modo) => (
            <label key={modo.valor} style={{ display: "block", marginBottom: 6 }}>
              <input
                type="radio"
                name="politica"
                checked={props.config.policy_mode === modo.valor}
                onChange={() => props.onPolitica(modo.valor)}
                style={{ width: "auto", marginRight: 8 }}
              />
              {modo.texto}
            </label>
          ))}
          <label style={{ display: "block", marginTop: 10 }}>
            <input
              type="checkbox"
              checked={false}
              onChange={(e) => props.onModo(e.target.checked)}
              style={{ width: "auto", marginRight: 8 }}
            />
            activar el modo seguro (sólo los agentes base y las primitivas)
          </label>
          {props.config.policy_mode === "unrestricted" && (
            <div className="aviso" style={{ marginTop: 10 }}>
              En «unrestricted» el runtime deja de preguntar. Los permisos siguen siendo explícitos por
              binario y ruta: no se concede nada que no se haya pedido antes.
            </div>
          )}
        </div>

        <div className="seccion">
          <h2>Sandbox</h2>
          <div className="sub">
            Nivel actual: <strong>{props.config.sandbox}</strong>. N0 es aislamiento de proceso (entorno
            limpio, sin red, con tiempo límite): <strong>no</strong> es aislamiento del sistema operativo.
            Sólo N1 (Docker, sin red y con el espacio de trabajo de sólo lectura) confina de verdad.
          </div>
        </div>
      </div>
    </div>
  );
}
