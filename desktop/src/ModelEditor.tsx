/**
 * Página para agregar o editar un proveedor/modelo. Vive en el área principal del
 * armazón —al lado de la barra lateral y bajo la cabecera de la app—, así que **no es un
 * modal**: no hay fondo oscurecido ni tarjeta flotante y ocupa todo el ancho disponible, con
 * «Volver» arriba y Guardar/Cancelar siempre a la vista en el pie. Comparte armazón con Ajustes
 * y el asistente inicial (ver «pantallas de configuración» en `styles.css`).
 *
 * Se organiza en cuatro secciones, con un índice lateral para saltar entre ellas:
 *   · General    — identidad del proveedor, familia, modelo y activación.
 *   · Conexión   — endpoint, credencial y cuenta.
 *   · Generación — esfuerzo de razonamiento y tope de salida.
 *   · Avanzado   — sólo lo que el runtime de verdad persiste y usa para ese modelo.
 *
 * Sólo se ofrecen ajustes que el runtime usa de verdad: el esfuerzo de razonamiento (dinámico
 * por modelo; «auto» no envía el parámetro) y el tope de salida (``max_tokens`` o
 * ``max_completion_tokens`` según el modelo). Temperatura, top_p, penalizaciones y timeout no
 * los lee todavía, así que **no** se ofrecen: no se persiste lo que se ignora.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  CatalogEntry,
  EffortMeta,
  ModelsResult,
  SavedProviderView,
  api,
  mensajeDe,
} from "./api";
import { useT, type Clave } from "./i18n";

interface Props {
  /** Proveedor a editar; `null` = agregar uno nuevo. */
  proveedor: SavedProviderView | null;
  /** `true` si el proveedor que se edita es el global activo: la activación queda fijada. */
  esActivo: boolean;
  /** Se llamó al backend con éxito: mensaje listo para el aviso de la app. */
  onGuardado: (mensaje: string) => void;
  onCerrar: () => void;
  onAviso: (mensaje: string) => void;
}

/** Índice de secciones: los anclajes son ids estables en el DOM (ver `aria`/`id` abajo). */
const SECCIONES: { id: string; titulo: Clave; resumen: Clave }[] = [
  {
    id: "modelo-general",
    titulo: "editor.section.general",
    resumen: "editor.section.general.summary",
  },
  {
    id: "modelo-conexion",
    titulo: "editor.section.connection",
    resumen: "editor.section.connection.summary",
  },
  {
    id: "modelo-generacion",
    titulo: "editor.section.generation",
    resumen: "editor.section.generation.summary",
  },
  {
    id: "modelo-avanzado",
    titulo: "editor.section.advanced",
    resumen: "editor.section.advanced.summary",
  },
];

/** Etiqueta legible de un nivel de esfuerzo; el id del nivel no se traduce. */
function etiquetaNivel(
  nivel: string,
  t: (clave: Clave, params?: Record<string, string | number>) => string,
): string {
  if (nivel === "auto") return t("editor.effort.auto");
  if (nivel === "off") return t("editor.effort.off");
  return nivel;
}

/** Familia de API que corresponde al nombre (el runtime sólo distingue anthropic). */
function familiaDeNombre(nombre: string): string {
  return nombre.trim().toLowerCase() === "anthropic" ? "anthropic" : "openai";
}

export function ModelEditor(props: Props) {
  const t = useT();
  const edicion = props.proveedor;
  const editando = edicion !== null;

  const [catalogo, setCatalogo] = useState<CatalogEntry[]>([]);
  const [elegido, setElegido] = useState("");
  const [nombre, setNombre] = useState(edicion?.name ?? "");
  const [familia, setFamilia] = useState(familiaDeNombre(edicion?.name ?? ""));
  const [url, setUrl] = useState(edicion?.base_url ?? "");
  const [modelo, setModelo] = useState(edicion?.model ?? "");
  const [variable, setVariable] = useState(edicion?.api_key_env ?? "");
  const [clave, setClave] = useState("");
  const [cuenta, setCuenta] = useState(edicion?.account_id ?? "");
  const [esfuerzo, setEsfuerzo] = useState(edicion?.reasoning_effort || "auto");
  // Interruptor Think: por defecto encendido (el proveedor suele activar razonamiento); sólo
  // se persiste/envía si el modelo declara un canal Think.
  const [think, setThink] = useState(edicion?.think ?? true);
  const [maxTokens, setMaxTokens] = useState(edicion?.max_tokens ? String(edicion.max_tokens) : "");
  // Al crear se guarda y activa siempre; al editar el activo, queda fijado; si no, lo decide
  // la persona con una casilla explícita (nada cambia el global por su cuenta).
  const [activar, setActivar] = useState(editando ? Boolean(props.esActivo) : true);
  const [modelos, setModelos] = useState<ModelsResult | null>(null);
  const [nota, setNota] = useState("");
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const datos = await api<{ catalog: CatalogEntry[] }>("/providers/catalog");
        setCatalogo(Array.isArray(datos.catalog) ? datos.catalog : []);
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

  // Niveles del modelo elegido: lo que anunció la API; si no, los del proveedor guardado
  // (que ya combinan catálogo de capacidades + overrides). Vacío = no admite esfuerzo.
  const niveles = useMemo(() => {
    if (!modelo) return [];
    const deApi = modelos?.effort_options?.[modelo];
    if (deApi && deApi.length > 0) return deApi;
    if (editando && edicion && modelo === edicion.model) return edicion.effort_options ?? [];
    return [];
  }, [modelo, modelos, editando, edicion]);

  const meta: EffortMeta | null = useMemo(() => {
    if (!modelo) return null;
    const deApi = modelos?.effort_meta?.[modelo];
    if (deApi) return deApi;
    if (editando && edicion && modelo === edicion.model) return edicion.effort_meta ?? null;
    return null;
  }, [modelo, modelos, editando, edicion]);

  // Al cambiar de modelo, un esfuerzo que ese modelo no admite vuelve a «auto».
  const claveNiveles = niveles.join(",");
  const anteriorModelo = useRef(modelo);
  useEffect(() => {
    const cambio = anteriorModelo.current !== modelo;
    anteriorModelo.current = modelo;
    setEsfuerzo((previo) =>
      cambio && previo !== "auto" && !niveles.includes(previo) ? "auto" : previo,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelo, claveNiveles]);

  function irASeccion(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

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
      setNota("");
    }
  }

  function aplicarResultado(resultado: ModelsResult, etiqueta: string) {
    if (resultado.ok && resultado.models.length > 0) {
      setModelos(resultado);
      setNota(
        t("editor.catalog.result", {
          n: resultado.models.length,
          etiqueta,
          url: resultado.url,
        }),
      );
      return;
    }
    if (resultado.auth_error) {
      const codigo = resultado.status || 401;
      setNota(
        t("editor.catalog.authError", { etiqueta, codigo, url: resultado.url }),
      );
      return;
    }
    if (resultado.ok) {
      setNota(t("editor.catalog.empty", { etiqueta, url: resultado.url }));
      return;
    }
    setNota(
      t("editor.catalog.error", {
        etiqueta,
        url: resultado.url,
        error: resultado.error ?? t("editor.noDetail"),
      }),
    );
  }

  async function cargarModelos() {
    if (!url.trim()) {
      setError(t("editor.error.baseUrl"));
      return;
    }
    setCargando(true);
    setNota("");
    setError("");
    try {
      // Con la clave escrita se usa el formulario; sin ella, y editando, las credenciales del
      // servidor del proveedor guardado (la clave nunca sale de ahí).
      const ruta =
        editando && !clave.trim()
          ? `/models?provider=${encodeURIComponent(edicion!.id)}`
          : `/models?base_url=${encodeURIComponent(url)}&family=${encodeURIComponent(familia)}&api_key=${encodeURIComponent(clave)}`;
      const resultado = await api<ModelsResult>(ruta);
      aplicarResultado(
        resultado,
        nombre.trim() || url.trim() || t("editor.theProvider"),
      );
      if (resultado.ok && resultado.models.length > 0 && resultado.suggested) {
        setModelo((actual) => actual || resultado.suggested || "");
      }
    } catch (exc) {
      setNota(mensajeDe(exc));
    } finally {
      setCargando(false);
    }
  }

  async function guardar() {
    if (!nombre.trim() || !modelo.trim() || !url.trim()) {
      setError(t("editor.error.required"));
      return;
    }
    let limite = 0;
    if (maxTokens.trim()) {
      const numero = Number(maxTokens);
      if (!Number.isFinite(numero) || numero < 0) {
        setError(t("editor.error.maxTokens"));
        return;
      }
      limite = Math.trunc(numero);
    }
    setError("");
    setGuardando(true);
    try {
      const info = modelo ? modelos?.capabilities?.[modelo] : undefined;
      const anunciado = info?.effort;
      const cuerpo = {
        name: nombre.trim(),
        model: modelo.trim(),
        base_url: url.trim(),
        api_key: clave,
        api_key_env: variable.trim(),
        account_id: cuenta.trim(),
        reasoning_effort: esfuerzo === "auto" ? "" : esfuerzo,
        think: meta?.think_param ? think : null,
        max_tokens: limite,
        // Niveles/channel que declaró el endpoint: el backend valida con ellos y los guarda
        // como capacidades del modelo, para que el adapter envíe el parámetro correcto.
        effort_levels: anunciado?.supported_levels ?? [],
        effort_control: anunciado?.control ?? info?.control ?? "",
        effort_can_disable: Boolean(info?.can_disable ?? anunciado?.can_disable),
      };
      let mensaje: string;
      if (editando && edicion) {
        await api(`/providers/${encodeURIComponent(edicion.id)}`, {
          method: "PATCH",
          body: cuerpo,
        });
        const activarAhora = activar && !props.esActivo;
        if (activarAhora) {
          await api("/providers/active", { method: "POST", body: { provider: edicion.id } });
        }
        mensaje = t("editor.saved.updated", {
          nombre: cuerpo.name,
          modelo: cuerpo.model,
          extra: activarAhora ? t("editor.saved.updated.activated") : "",
        });
      } else {
        await api(`/providers`, { method: "POST", body: cuerpo });
        mensaje = t("editor.saved.created", { nombre: cuerpo.name, modelo: cuerpo.model });
      }
      setClave("");
      props.onGuardado(mensaje);
    } catch (exc) {
      setError(mensajeDe(exc));
    } finally {
      setGuardando(false);
    }
  }

  const puedeGuardar = Boolean(nombre.trim() && modelo.trim() && url.trim());

  return (
    <div className="editor">
      <header className="editor-cabecera">
        <button className="quiet" onClick={props.onCerrar} title={t("editor.header.back.title")}>
          {t("editor.header.back")}
        </button>
        <h2>{editando ? t("editor.title.edit") : t("editor.title.add")}</h2>
        <span className="nota" style={{ margin: 0 }}>
          {editando
            ? `${edicion?.name} · ${edicion?.model || t("editor.model.none")}`
            : t("editor.newProvider")}
        </span>
      </header>

      <div className="editor-cuerpo">
        <p className="nota" role="note">{t("editor.global.note")}</p>
        <nav className="editor-indice" aria-label={t("editor.sections.aria")}>
          {SECCIONES.map((seccion) => (
            <a
              key={seccion.id}
              href={`#${seccion.id}`}
              onClick={(evento) => {
                evento.preventDefault();
                irASeccion(seccion.id);
              }}
            >
              <span className="seccion-nombre">{t(seccion.titulo)}</span>
              <span className="seccion-resumen">{t(seccion.resumen)}</span>
            </a>
          ))}
        </nav>

        <div className="editor-secciones">
          {/* ------------------------------------------------------ General */}
          <section className="tarjeta" id="modelo-general">
            <h3>{t("editor.section.general")}</h3>

            <div className="campo">
              <label>{t("editor.catalog.label")}</label>
              <select value={elegido} onChange={(evento) => elegirDelCatalogo(evento.target.value)}>
                <option value="">{t("editor.catalog.choose")}</option>
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
                <label>{t("editor.name.label")}</label>
                <input
                  value={nombre}
                  onChange={(evento) => setNombre(evento.target.value)}
                  placeholder="openai"
                />
              </div>
              <div className="campo">
                <label>{t("editor.family.label")}</label>
                <select value={familia} onChange={(evento) => setFamilia(evento.target.value)}>
                  <option value="openai">{t("editor.family.openai")}</option>
                  <option value="anthropic">{t("editor.family.anthropic")}</option>
                </select>
              </div>
            </div>

            <div className="rejilla" style={{ alignItems: "end" }}>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("editor.model.label")}</label>
                <input
                  value={modelo}
                  onChange={(evento) => setModelo(evento.target.value)}
                  placeholder={t("editor.model.placeholder")}
                />
              </div>
              {/* Think + esfuerzo, junto al modelo elegido (el picker del modelo). */}
              <div
                className="campo"
                style={{ marginBottom: 0, display: "flex", gap: 10, alignItems: "center" }}
              >
                <label
                  className="opcion"
                  title={
                    meta?.think_param
                      ? t("editor.think.title", { canal: meta.think_param })
                      : t("editor.think.none")
                  }
                >
                  <input
                    type="checkbox"
                    checked={think}
                    disabled={!meta?.think_param}
                    onChange={(evento) => setThink(evento.target.checked)}
                  />
                  Think
                </label>
                <select
                  value={esfuerzo}
                  disabled={niveles.length === 0}
                  aria-label={t("editor.effort.aria")}
                  title={t("editor.effort.title")}
                  onChange={(evento) => setEsfuerzo(evento.target.value)}
                >
                  {niveles.map((nivel) => (
                    <option key={nivel} value={nivel}>
                      {etiquetaNivel(nivel, t)}
                    </option>
                  ))}
                  {esfuerzo !== "auto" && !niveles.includes(esfuerzo) && (
                    <option value={esfuerzo}>{t("editor.effort.saved", { nivel: esfuerzo })}</option>
                  )}
                  {niveles.length === 0 && (
                    <option value="auto">{t("editor.effort.none")}</option>
                  )}
                </select>
              </div>
            </div>
            <span className="nota">
              {modelo
                ? meta?.think_param
                  ? `${t("editor.think.title", { canal: meta.think_param })}${
                      meta.inferred ? t("editor.think.inferred") : ""
                    }`
                  : t("editor.think.noneAndLevels")
                : t("editor.think.hint")}
            </span>

            {modelos?.ok && modelos.models.length > 0 && (
              <div className="lista-modelos">
                {modelos.models.map((item) => (
                  <button
                    key={item}
                    className={item === modelo ? "modelo elegido" : "modelo"}
                    onClick={() => setModelo(item)}
                  >
                    {item}
                    {item === modelos.suggested ? t("common.mostUsed") : ""}
                  </button>
                ))}
              </div>
            )}

            <div>
              <p className="etiqueta">{t("editor.activation")}</p>
              {!editando && (
                <>
                  <label className="opcion">
                    <input type="checkbox" checked readOnly />{" "}
                    {t("editor.activation.saveAndActivate")}
                  </label>
                  <span className="nota">{t("editor.activation.saveNote")}</span>
                </>
              )}
              {editando && props.esActivo && (
                <>
                  <label className="opcion">
                    <input type="checkbox" checked readOnly /> {t("editor.activation.isActive")}
                  </label>
                  <span className="nota">{t("editor.activation.rolesNote")}</span>
                </>
              )}
              {editando && !props.esActivo && (
                <>
                  <label className="opcion">
                    <input
                      type="checkbox"
                      checked={activar}
                      onChange={(evento) => setActivar(evento.target.checked)}
                    />
                    {t("editor.activation.activateOnSave")}
                  </label>
                  <span className="nota">{t("editor.activation.activateNote")}</span>
                </>
              )}
            </div>
          </section>

          {/* ----------------------------------------------------- Conexión */}
          <section className="tarjeta" id="modelo-conexion">
            <h3>{t("editor.section.connection")}</h3>

            <div className="campo">
              <label>
                {t("editor.baseUrl")}
                {catalogo.find((c) => c.key === elegido)?.needs_edit
                  ? t("editor.baseUrl.needsEdit")
                  : ""}
              </label>
              <input
                value={url}
                onChange={(evento) => setUrl(evento.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </div>

            <div className="rejilla">
              <div className="campo">
                <label>{t("editor.envVar")}</label>
                <input
                  value={variable}
                  onChange={(evento) => setVariable(evento.target.value)}
                  placeholder="OPENAI_API_KEY"
                />
              </div>
              <div className="campo">
                <label>{editando ? t("editor.key.new") : t("editor.key.or")}</label>
                <input
                  type="password"
                  value={clave}
                  onChange={(evento) => setClave(evento.target.value)}
                  placeholder={editando ? t("editor.key.placeholder.edit") : "sk-…"}
                />
              </div>
            </div>

            <div className="campo">
              <label>{t("editor.account")}</label>
              <input
                value={cuenta}
                onChange={(evento) => setCuenta(evento.target.value)}
                placeholder={t("editor.account.placeholder")}
              />
            </div>

            <div className="acciones">
              <button disabled={!url.trim() || cargando} onClick={() => void cargarModelos()}>
                {cargando ? t("app.loading") : t("editor.loadModels")}
              </button>
              {editando && (
                <span className="nota" style={{ margin: 0, alignSelf: "center" }}>
                  {t("editor.key.savedNote")}
                </span>
              )}
            </div>

            {nota && <p className="nota">{nota}</p>}
          </section>

          {/* -------------------------------------------------- Generación */}
          <section className="tarjeta" id="modelo-generacion">
            <h3>{t("editor.section.generation")}</h3>

            <p className="nota" style={{ marginTop: 0 }}>
              {t("editor.generation.note")}
            </p>

            <div className="campo" style={{ marginBottom: 0 }}>
              <label>{t("editor.maxTokens.label")}</label>
              <input
                type="number"
                min={0}
                value={maxTokens}
                onChange={(evento) => setMaxTokens(evento.target.value)}
                placeholder={t("editor.maxTokens.placeholder")}
              />
              <span className="nota">
                {t("editor.maxTokens.note.a")}
                <code>max_tokens</code>
                {t("editor.maxTokens.note.b")}
                <code>max_completion_tokens</code>
                {t("editor.maxTokens.note.c")}
              </span>
            </div>
          </section>

          {/* --------------------------------------------------- Avanzado */}
          <section className="tarjeta" id="modelo-avanzado">
            <h3>{t("editor.section.advanced")}</h3>
            <p className="nota" style={{ marginTop: 0 }}>
              {t("editor.advanced.note")}
            </p>

            {meta?.control || niveles.length > 0 ? (
              <ul className="lista-capacidades">
                {meta && meta.control && (
                  <li>
                    {t("editor.advanced.channel")}
                    <code>{meta.control}</code>
                  </li>
                )}
                {niveles.length > 0 && (
                  <li>{t("editor.advanced.levels", { niveles: niveles.join(", ") })}</li>
                )}
                {meta && (
                  <li>
                    {t("editor.advanced.canDisable", {
                      valor: meta.can_disable ? t("editor.yes") : t("editor.no"),
                    })}
                  </li>
                )}
                {meta?.inferred && <li>{t("editor.advanced.origin")}</li>}
              </ul>
            ) : (
              <div className="vacio" style={{ padding: 0 }}>
                {modelo ? t("editor.advanced.empty.none") : t("editor.advanced.empty.choose")}
              </div>
            )}

            <p className="nota">{t("editor.advanced.footer")}</p>
          </section>
        </div>
      </div>

      <footer className="editor-acciones">
        <button
          className="primary"
          disabled={guardando || !puedeGuardar}
          onClick={() => void guardar()}
        >
          {guardando
            ? t("editor.saving")
            : editando
              ? t("editor.saveChanges")
              : t("editor.saveAndActivate")}
        </button>
        <button className="quiet" disabled={guardando} onClick={props.onCerrar}>
          {t("common.cancel")}
        </button>
        {error && <span className="editor-error">{error}</span>}
      </footer>
    </div>
  );
}
