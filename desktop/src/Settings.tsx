/**
 * Ajustes, agrupados por función: Proveedor, Roles, Seguridad y Mantenimiento.
 * Todo lo que la TUI tiene repartido en comandos, en cuatro tarjetas.
 */

import { useEffect, useMemo, useState } from "react";

import { CatalogEntry, ConfigView, ModelsResult, RolesView, api, mensajeDe } from "./api";

interface Props {
  config: ConfigView;
  safeMode: boolean;
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
  onConfigCambiada: () => void;
  onAviso: (mensaje: string) => void;
  onConfirmar: (titulo: string, detalle: string, accion: () => void) => void;
}

const MODOS: { valor: string; texto: string }[] = [
  { valor: "ask", texto: "ask — pregunta cada permiso" },
  { valor: "auto", texto: "auto — aprueba lo de bajo riesgo" },
  { valor: "unrestricted", texto: "unrestricted — no pregunta nada" },
];

export function Settings(props: Props) {
  const [catalogo, setCatalogo] = useState<CatalogEntry[]>([]);
  const [roles, setRoles] = useState<RolesView | null>(null);
  const [clave, setClave] = useState("");
  const [elegido, setElegido] = useState("");
  const [nombre, setNombre] = useState("");
  const [familia, setFamilia] = useState("openai");
  const [url, setUrl] = useState("");
  const [modelo, setModelo] = useState("");
  const [variable, setVariable] = useState("");
  const [modelos, setModelos] = useState<ModelsResult | null>(null);
  const [cargando, setCargando] = useState(false);
  const [salida, setSalida] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const [cat, rol] = await Promise.all([
          api<{ catalog: CatalogEntry[] }>("/providers/catalog"),
          api<RolesView>("/roles"),
        ]);
        setCatalogo(cat.catalog);
        setRoles(rol);
      } catch (exc) {
        props.onAviso(mensajeDe(exc));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const grupos = useMemo(() => {
    const mapa = new Map<string, CatalogEntry[]>();
    for (const entrada of catalogo) {
      const lista = mapa.get(entrada.group) ?? [];
      lista.push(entrada);
      mapa.set(entrada.group, lista);
    }
    return [...mapa.entries()];
  }, [catalogo]);

  function elegirDelCatalogo(claveEntrada: string) {
    setElegido(claveEntrada);
    const entrada = catalogo.find((item) => item.key === claveEntrada);
    if (entrada) {
      setNombre(entrada.key);
      setFamilia(entrada.family);
      setUrl(entrada.base_url);
      setVariable(entrada.key.toUpperCase().replace("-", "_") + "_API_KEY");
      setModelos(null);
      setModelo("");
      setSalida("");
    }
  }

  async function cargarModelos() {
    setCargando(true);
    setSalida("");
    try {
      const resultado = await api<ModelsResult>(
        `/models?base_url=${encodeURIComponent(url)}&family=${encodeURIComponent(familia)}&api_key=${encodeURIComponent(clave)}`,
      );
      setModelos(resultado);
      if (!resultado.ok) {
        setSalida(`no pude listar modelos en ${resultado.url}: ${resultado.error ?? "sin detalle"}`);
      } else if (resultado.models.length === 0) {
        setSalida(`${resultado.url} respondió sin modelos`);
      } else if (resultado.suggested) {
        setModelo((actual) => actual || resultado.suggested || "");
        setSalida(`${resultado.models.length} modelos; sugerido: ${resultado.suggested}`);
      }
    } catch (exc) {
      setSalida(mensajeDe(exc));
    } finally {
      setCargando(false);
    }
  }

  async function aplicarRol(rol: string, proveedor: string, model: string) {
    try {
      const vista = await api<RolesView>("/roles", {
        method: "PUT",
        body: { role: rol, provider: proveedor, model },
      });
      setRoles(vista);
      props.onConfigCambiada();
      if (vista.runtime_error) {
        props.onAviso(`rol ${rol} guardado, pero el runtime no se pudo rearmar: ${vista.runtime_error}`);
      } else {
        props.onAviso(`rol ${rol} aplicado`);
      }
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  async function diagnostico(ruta: string, titulo: string, metodo = "GET") {
    try {
      const datos = await api<Record<string, unknown>>(ruta, { method: metodo });
      setSalida(`${titulo}\n\n${JSON.stringify(datos, null, 2)}`);
      props.onConfigCambiada();
    } catch (exc) {
      setSalida(`${titulo}\n\n${mensajeDe(exc)}`);
    }
  }

  return (
    <div className="ajustes" onClick={props.onCerrar}>
      <div className="hoja" onClick={(evento) => evento.stopPropagation()}>
        <div className="cabecera-hoja">
          <h2>Ajustes</h2>
          <button className="quiet" onClick={props.onCerrar}>
            cerrar
          </button>
        </div>

        {/* ------------------------------------------------ proveedor */}
        <section className="tarjeta">
          <h3>Proveedor</h3>
          <div className="campo">
            <label>del catálogo (trae la URL puesta)</label>
            <select value={elegido} onChange={(evento) => elegirDelCatalogo(evento.target.value)}>
              <option value="">— elegir proveedor —</option>
              {grupos.map(([grupo, entradas]) => (
                <optgroup key={grupo} label={grupo}>
                  {entradas.map((entrada) => (
                    <option key={entrada.key} value={entrada.key}>
                      {entrada.label}
                      {entrada.note ? ` · ${entrada.note}` : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
          <div className="rejilla">
            <div className="campo">
              <label>nombre (familia del proveedor)</label>
              <input value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="openai" />
            </div>
            <div className="campo">
              <label>familia de API</label>
              <select value={familia} onChange={(e) => setFamilia(e.target.value)}>
                <option value="openai">openai (compatible)</option>
                <option value="anthropic">anthropic</option>
              </select>
            </div>
          </div>
          <div className="campo">
            <label>URL base{catalogo.find((c) => c.key === elegido)?.needs_edit ? " (este proveedor pide editarla)" : ""}</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
          </div>
          <div className="rejilla">
            <div className="campo">
              <label>variable de entorno (recomendado)</label>
              <input value={variable} onChange={(e) => setVariable(e.target.value)} placeholder="OPENAI_API_KEY" />
            </div>
            <div className="campo">
              <label>o la clave (no se vuelve a mostrar)</label>
              <input type="password" value={clave} onChange={(e) => setClave(e.target.value)} placeholder="sk-…" />
            </div>
          </div>

          <div className="acciones">
            <button disabled={!url.trim() || cargando} onClick={() => void cargarModelos()}>
              {cargando ? "cargando…" : "Cargar modelos"}
            </button>
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
          </div>

          <div className="campo" style={{ marginTop: 10 }}>
            <label>modelo</label>
            <input
              value={modelo}
              onChange={(e) => setModelo(e.target.value)}
              placeholder="elige de la lista o escríbelo"
            />
          </div>

          {salida && <p className="nota">{salida}</p>}
          {modelos?.ok && modelos.models.length > 0 && (
            <div className="lista-modelos">
              {modelos.models.map((item) => (
                <button
                  key={item}
                  className={item === modelo ? "modelo elegido" : "modelo"}
                  onClick={() => setModelo(item)}
                >
                  {item}
                  {item === modelos.suggested ? "  · más usado" : ""}
                </button>
              ))}
            </div>
          )}

          <p className="nota">
            Guardados: {props.config.providers.length} · activo: {props.config.active_provider || "ninguno"}.
            La clave nunca se devuelve: sólo se sabe si está puesta.
          </p>
        </section>

        {/* ------------------------------------------------ roles */}
        <section className="tarjeta">
          <h3>Roles</h3>
          <p className="nota">
            Cada rol se aplica por separado, con su propio botón: aplicar uno no cambia los demás.
          </p>
          {roles?.roles.map((rol) => (
            <FilaRol
              key={rol}
              rol={rol}
              roles={roles}
              onAplicar={(proveedor, model) => void aplicarRol(rol, proveedor, model)}
            />
          ))}
        </section>

        {/* ------------------------------------------------ seguridad */}
        <section className="tarjeta">
          <h3>Seguridad</h3>
          {MODOS.map((modo) => (
            <label key={modo.valor} className="opcion">
              <input
                type="radio"
                name="politica"
                checked={props.config.policy_mode === modo.valor}
                onChange={() => props.onPolitica(modo.valor)}
              />
              {modo.texto}
            </label>
          ))}
          <label className="opcion">
            <input
              type="checkbox"
              checked={props.safeMode}
              onChange={(e) => props.onModo(e.target.checked)}
            />
            modo seguro (sólo agentes base y primitivas)
          </label>
          <p className="nota">
            Sandbox <strong>{props.config.sandbox}</strong>. N0 es aislamiento de proceso (entorno
            limpio, sin red, con tiempo límite): <strong>no</strong> aísla del sistema operativo. Sólo
            N1 (Docker, sin red, espacio de trabajo en sólo lectura) confina de verdad.
          </p>
          <div className="acciones">
            <button onClick={() => void diagnostico("/setup", "setup (sandbox)", "POST")}>
              Detectar Docker (setup)
            </button>
          </div>
        </section>

        {/* ------------------------------------------------ mantenimiento */}
        <section className="tarjeta">
          <h3>Mantenimiento</h3>
          <div className="acciones">
            <button onClick={() => void diagnostico("/doctor", "doctor")}>Diagnóstico</button>
            <button onClick={() => void diagnostico("/stats", "stats")}>Métricas</button>
            <button onClick={() => void diagnostico("/compact", "compactar", "POST")}>
              Compactar contexto
            </button>
            <button onClick={() => void diagnostico("/snapshots", "snapshots")}>Snapshots</button>
          </div>
          {salida && <pre className="salida">{salida}</pre>}
          <div className="acciones">
            <button
              onClick={() =>
                props.onConfirmar(
                  "Reset del estado base",
                  "Borra los agentes y las skills creados en este proyecto. Las skills del sistema se conservan.",
                  () => void diagnostico("/reset", "reset", "POST"),
                )
              }
            >
              Reset…
            </button>
          </div>
          <p className="nota">
            Restaurar un snapshot borra el estado actual: se pide confirmación con lo que se
            descarta (añade el nombre del snapshot en el comando de mantenimiento).
          </p>
        </section>

        {/* ------------------------------------------------ configuración cruda */}
        <section className="tarjeta">
          <h3>Configuración</h3>
          <pre className="salida">{JSON.stringify(props.config.config, null, 2)}</pre>
        </section>
      </div>
    </div>
  );
}

interface FilaProps {
  rol: string;
  roles: RolesView;
  onAplicar: (proveedor: string, model: string) => void;
}

function FilaRol(props: FilaProps) {
  const actual = props.roles.configured[props.rol] ?? {};
  const [proveedor, setProveedor] = useState(actual.provider ?? "");
  const [model, setModel] = useState(actual.model ?? "");

  useEffect(() => {
    setProveedor(actual.provider ?? "");
    setModel(actual.model ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actual.provider, actual.model]);

  return (
    <div className="rejilla" style={{ marginTop: 8, alignItems: "end" }}>
      <div className="campo" style={{ marginBottom: 0 }}>
        <label>
          {props.rol}
          {actual.provider ? "" : " (usa el global)"}
        </label>
        <select value={proveedor} onChange={(e) => setProveedor(e.target.value)}>
          <option value="">— el global ({props.roles.active || "ninguno"}) —</option>
          {props.roles.providers.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} · {item.model ?? item.id}
            </option>
          ))}
        </select>
      </div>
      <div className="campo" style={{ marginBottom: 0, display: "flex", gap: 8 }}>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="modelo (opcional)"
        />
        <button onClick={() => props.onAplicar(proveedor, model)}>Aplicar</button>
      </div>
    </div>
  );
}
