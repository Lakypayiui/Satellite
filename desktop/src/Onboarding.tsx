/**
 * Asistente inicial: elegir la carpeta del proyecto y configurar el proveedor, en un solo
 * paso. Se abre solo la primera vez (si no hay proyecto elegido o no hay proveedor) y queda
 * accesible desde la paleta con `/onboarding`.
 *
 * Es una página, no un diálogo: sustituye el contenido del área principal, con la barra lateral
 * y la cabecera de la app a la vista, así que no tapa la aplicación mientras se configura.
 *
 * Reutiliza los mismos endpoints que Ajustes (`/providers/catalog`, `/models`, `/providers`)
 * y la misma redacción: la clave nunca se devuelve y sólo se sabe si está puesta. Nada se
 * inventa: el estado del runtime que se muestra es el que reporta el backend.
 *
 * Todo el texto visible sale del i18n tipado (`useT`); los valores técnicos (URL, variable de
 * entorno, id de proveedor) se dejan tal cual.
 */

import { useEffect, useMemo, useState } from "react";

import { CatalogEntry, ModelsResult, api, mensajeDe } from "./api";
import { useT } from "./i18n";

interface Props {
  ruta: string;
  projectExplicit: boolean;
  providerConfigured: boolean;
  runtimeReady: boolean;
  runtimeError: string;
  onAbrirProyecto: (ruta?: string) => void;
  onGuardarProveedor: (datos: {
    name: string;
    model: string;
    base_url: string;
    api_key: string;
    api_key_env: string;
  }) => void;
  onReintentar: () => void;
  onAviso: (mensaje: string) => void;
  onCerrar: () => void;
}

export function Onboarding(props: Props) {
  const t = useT();
  const [catalogo, setCatalogo] = useState<CatalogEntry[]>([]);
  const [elegido, setElegido] = useState("");
  const [nombre, setNombre] = useState("");
  const [familia, setFamilia] = useState("openai");
  const [url, setUrl] = useState("");
  const [modelo, setModelo] = useState("");
  const [variable, setVariable] = useState("");
  const [clave, setClave] = useState("");
  const [modelos, setModelos] = useState<ModelsResult | null>(null);
  const [nota, setNota] = useState("");
  const [probando, setProbando] = useState(false);

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

  function elegirDelCatalogo(claveEntrada: string) {
    setElegido(claveEntrada);
    const entrada = catalogo.find((item) => item.key === claveEntrada);
    if (entrada) {
      setNombre(entrada.key);
      setFamilia(entrada.family);
      setUrl(entrada.base_url);
      setVariable(entrada.key.toUpperCase().replace("-", "_") + "_API_KEY");
      setModelo("");
      setModelos(null);
      setNota("");
    }
  }

  /** Prueba la conexión listando modelos y, si puede, sugiere uno. */
  async function probar() {
    setProbando(true);
    setNota("");
    try {
      const resultado = await api<ModelsResult>(
        `/models?base_url=${encodeURIComponent(url)}&family=${encodeURIComponent(familia)}&api_key=${encodeURIComponent(clave)}`,
      );
      if (resultado.ok && resultado.models.length > 0) {
        setModelos(resultado);
        setModelo((actual) => actual || resultado.suggested || resultado.models[0] || "");
        setNota(
          t("onboarding.test.ok", { n: resultado.models.length, url: resultado.url }),
        );
        return;
      }
      if (resultado.auth_error) {
        // 401/403 se dice aparte y NO se borra la lista que ya hubiera.
        const codigo = resultado.status || 401;
        setNota(
          t("onboarding.test.authError", {
            etiqueta: nombre.trim() || t("editor.theProvider"),
            codigo,
            url: resultado.url,
          }),
        );
        return;
      }
      if (resultado.ok) {
        setNota(t("onboarding.test.empty", { url: resultado.url }));
        return;
      }
      setNota(
        t("onboarding.test.error", {
          url: resultado.url,
          error: resultado.error ?? t("editor.noDetail"),
        }),
      );
    } catch (exc) {
      setNota(mensajeDe(exc));
    } finally {
      setProbando(false);
    }
  }

  function guardar() {
    props.onGuardarProveedor({
      name: nombre.trim(),
      model: modelo.trim(),
      base_url: url.trim(),
      api_key: clave,
      api_key_env: variable.trim(),
    });
    setClave("");
  }

  const puedeGuardar = Boolean(nombre.trim() && modelo.trim() && url.trim());

  return (
    <div className="pantalla">
      <header className="pantalla-cabecera">
        <button className="quiet" onClick={props.onCerrar} title={t("common.escClose")}>
          {t("onboarding.later")}
        </button>
        <h2>{t("onboarding.title")}</h2>
        <span className="nota">{t("onboarding.subtitle")}</span>
      </header>

      <div className="pantalla-cuerpo">
        {/* 1 · proyecto */}
        <section className="tarjeta">
          <h3>{t("onboarding.project.title")}</h3>
          <p className="nota">
            {props.projectExplicit ? t("onboarding.project.open") : t("onboarding.project.none")}
          </p>
          <div className="item" style={{ cursor: "default" }} title={props.ruta}>
            <span className="nombre">{props.ruta || "—"}</span>
          </div>
          <div className="acciones">
            <button onClick={() => props.onAbrirProyecto()}>{t("onboarding.project.choose")}</button>
          </div>
        </section>

        {/* 2 · proveedor */}
        <section className="tarjeta">
          <h3>{t("onboarding.provider.title")}</h3>
          <p className="nota">{t("onboarding.provider.note")}</p>
          <div className="campo">
            <label>{t("onboarding.provider.catalog")}</label>
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
              <label>{t("onboarding.provider.name")}</label>
              <input
                value={nombre}
                onChange={(evento) => setNombre(evento.target.value)}
                placeholder="openai"
              />
            </div>
            <div className="campo">
              <label>{t("onboarding.provider.family")}</label>
              <select value={familia} onChange={(evento) => setFamilia(evento.target.value)}>
                <option value="openai">{t("onboarding.family.openai")}</option>
                <option value="anthropic">{t("onboarding.family.anthropic")}</option>
              </select>
            </div>
          </div>
          <div className="campo">
            <label>{t("editor.baseUrl")}</label>
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
              <label>{t("editor.key.or")}</label>
              <input
                type="password"
                value={clave}
                onChange={(evento) => setClave(evento.target.value)}
                placeholder="sk-…"
              />
            </div>
          </div>
          <div className="acciones">
            <button disabled={!url.trim() || probando} onClick={() => void probar()}>
              {probando ? t("onboarding.testing") : t("onboarding.test")}
            </button>
          </div>
          <div className="campo">
            <label>{t("editor.model.label")}</label>
            <input
              value={modelo}
              onChange={(evento) => setModelo(evento.target.value)}
              placeholder={t("editor.model.placeholder")}
            />
          </div>
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
          {nota && <p className="nota">{nota}</p>}
          <div className="acciones">
            <button className="primary" disabled={!puedeGuardar} onClick={guardar}>
              {t("editor.saveAndActivate")}
            </button>
          </div>
        </section>

        {/* 3 · estado */}
        <section className="tarjeta">
          <h3>{t("onboarding.state.title")}</h3>
          <p className="nota">
            {props.runtimeReady
              ? t("onboarding.state.ready")
              : props.providerConfigured
                ? t("onboarding.state.providerNoRuntime", {
                    error: props.runtimeError || t("editor.noDetail"),
                  })
                : t("onboarding.state.noProvider")}
          </p>
          <div className="acciones">
            <button onClick={props.onReintentar}>{t("onboarding.state.recheck")}</button>
            {props.runtimeReady && (
              <button className="primary" onClick={props.onCerrar}>
                {t("onboarding.state.start")}
              </button>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
