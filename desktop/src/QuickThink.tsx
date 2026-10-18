/**
 * Edición rápida desde la píldora del modelo activo en la cabecera.
 *
 * La píldora mantiene el **mismo texto y tamaño compacto** que el selector anterior
 * (`proveedor · modelo`), pero ahora es un botón accesible que abre un popover con:
 * proveedor activo, **modelo ofrecido por la API del proveedor** (con refresco) y el
 * interruptor Think + nivel de esfuerzo.
 *
 * No hay lógica nueva de capacidades: se reutilizan `effort_options`/`effort_meta` del
 * payload (mismo cálculo que Ajustes) y el catálogo real de `GET /models?provider=<id>`.
 * Persistir usa `PATCH /providers/{id}`, que valida contra el catálogo, conserva la clave y
 * rearma el runtime; nada de esto toca seguridad ni sandbox/contexto.
 *
 * Todo el texto visible sale del i18n tipado (`useT`); los ids de proveedor/modelo, las marcas
 * de nivel de esfuerzo y los errores técnicos del backend se dejan tal cual.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { ConfigView, ModelsResult, SavedProviderView, api, mensajeDe } from "./api";
import { useT } from "./i18n";

interface Props {
  /** Proveedor activo cuyo modelo/Think/esfuerzo se edita. */
  provider: SavedProviderView;
  /** Todos los guardados, para poder cambiar de activo sin salir del popover. */
  providers: SavedProviderView[];
  /** Fija otro proveedor global (mismo camino que el selector de cabecera). */
  onActivar: (id: string) => void;
  /** Config ya recargada por el backend tras guardar. */
  onCambiado: (vista: ConfigView) => void;
  onAviso: (mensaje: string) => void;
}

export function QuickThink(props: Props) {
  const t = useT();
  const [abierto, setAbierto] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [modelos, setModelos] = useState<ModelsResult | null>(null);
  const [cargandoModelos, setCargandoModelos] = useState(false);
  const [recarga, setRecarga] = useState(0);
  const caja = useRef<HTMLDivElement>(null);

  const [modeloSel, setModeloSel] = useState(props.provider.model ?? "");
  const [think, setThink] = useState<boolean>(props.provider.think ?? true);
  const [esfuerzo, setEsfuerzo] = useState(props.provider.reasoning_effort || "auto");

  const actual = props.provider.model ?? "";
  const pendiente = modeloSel !== actual;
  // Texto original y compacto: sólo «proveedor · modelo».
  const etiqueta = `${props.provider.name}${props.provider.model ? ` · ${props.provider.model}` : ""}`;

  // Capacidades del modelo elegido: lo que anunció/calculó el endpoint; si es el modelo
  // guardado y no vino lista, las del propio proveedor (mismo payload que Ajustes).
  const meta = useMemo(() => {
    const deApi = modeloSel ? modelos?.effort_meta?.[modeloSel] : undefined;
    if (deApi) return deApi;
    if (modeloSel && modeloSel === actual) return props.provider.effort_meta ?? null;
    return null;
  }, [modelos, modeloSel, actual, props.provider.effort_meta]);

  const niveles = useMemo(() => {
    const deApi = modeloSel ? modelos?.effort_options?.[modeloSel] : undefined;
    if (deApi && deApi.length > 0) return deApi;
    if (modeloSel && modeloSel === actual) return props.provider.effort_options ?? [];
    return [];
  }, [modelos, modeloSel, actual, props.provider.effort_options]);

  const canalThink = meta?.think_param ?? "";

  // El backend manda: si cambia el proveedor/activo o su config, el popover se sincroniza.
  useEffect(() => {
    setModeloSel(props.provider.model ?? "");
    setThink(props.provider.think ?? true);
    setEsfuerzo(props.provider.reasoning_effort || "auto");
  }, [
    props.provider.id,
    props.provider.model,
    props.provider.think,
    props.provider.reasoning_effort,
  ]);

  // Si el modelo elegido no admite el esfuerzo guardado, vuelve a «auto».
  const claveNiveles = niveles.join(",");
  useEffect(() => {
    setEsfuerzo((previo) => (previo !== "auto" && !niveles.includes(previo) ? "auto" : previo));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claveNiveles]);

  // Catálogo real del proveedor activo (sin inventar): se pide al abrir y al refrescar.
  useEffect(() => {
    if (!abierto) return;
    let vigente = true;
    setCargandoModelos(true);
    void api<ModelsResult>(`/models?provider=${encodeURIComponent(props.provider.id)}`)
      .then((datos) => {
        if (vigente) setModelos(datos);
      })
      .catch(() => {
        if (vigente) setModelos(null);
      })
      .finally(() => {
        if (vigente) setCargandoModelos(false);
      });
    return () => {
      vigente = false;
    };
  }, [abierto, props.provider.id, recarga]);

  // Cerrar al hacer clic fuera o con Escape (no secuestra Ctrl+K ni otras píldoras).
  useEffect(() => {
    if (!abierto) return;
    function alClic(evento: MouseEvent) {
      if (caja.current && !caja.current.contains(evento.target as Node)) setAbierto(false);
    }
    function alTeclado(evento: KeyboardEvent) {
      if (evento.key === "Escape") setAbierto(false);
    }
    document.addEventListener("mousedown", alClic);
    document.addEventListener("keydown", alTeclado);
    return () => {
      document.removeEventListener("mousedown", alClic);
      document.removeEventListener("keydown", alTeclado);
    };
  }, [abierto]);

  async function guardar(patch: Record<string, unknown>, mensaje: string) {
    setGuardando(true);
    try {
      const vista = await api<ConfigView>(`/providers/${encodeURIComponent(props.provider.id)}`, {
        method: "PATCH",
        body: patch,
      });
      props.onCambiado(vista);
      props.onAviso(
        vista.runtime_error
          ? t("settings.desktop.savedNoRuntime", { error: vista.runtime_error })
          : mensaje,
      );
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    } finally {
      setGuardando(false);
    }
  }

  function cambiarThink(valor: boolean) {
    setThink(valor);
    // Con un modelo distinto pendiente de aplicar, no se persiste todavía.
    if (!pendiente) {
      void guardar({ think: valor }, valor ? t("quick.think.on") : t("quick.think.off"));
    }
  }

  function cambiarEsfuerzo(valor: string) {
    setEsfuerzo(valor);
    if (!pendiente) {
      void guardar(
        { reasoning_effort: valor === "auto" ? "" : valor },
        t("quick.effort.saved", { valor }),
      );
    }
  }

  /** Persiste el modelo elegido con sus capacidades, conservando la clave del proveedor. */
  async function aplicarModelo() {
    if (!modeloSel || !pendiente) return;
    const info = modelos?.capabilities?.[modeloSel];
    const anunciado = info?.effort;
    const control = info?.control ?? anunciado?.control ?? meta?.control ?? "";
    await guardar(
      {
        model: modeloSel,
        reasoning_effort: esfuerzo === "auto" ? "" : esfuerzo,
        think: canalThink ? think : null,
        effort_levels: anunciado?.supported_levels ?? [],
        effort_control: control,
        effort_can_disable: Boolean(
          info?.can_disable ?? anunciado?.can_disable ?? meta?.can_disable,
        ),
      },
      t("quick.model.applied", { modelo: modeloSel }),
    );
  }

  const opciones = modelos?.ok ? modelos.models : [];
  const sinLista = modelos !== null && (!modelos.ok || opciones.length === 0);
  const motivoLista = modelos?.auth_error
    ? t("quick.list.auth", { codigo: modelos.status ?? 401 })
    : modelos?.error
      ? t("quick.list.error", { error: modelos.error })
      : t("quick.list.none");

  return (
    <div className="quick-think" ref={caja}>
      <button
        className="chip-sel"
        onClick={() => setAbierto((valor) => !valor)}
        aria-haspopup="menu"
        aria-expanded={abierto}
        aria-label={t("quick.aria", { etiqueta })}
        title={t("quick.title", { etiqueta })}
      >
        {etiqueta}
      </button>

      {abierto && (
        <div
          className="quick-think-pop"
          role="menu"
          aria-label={t("quick.menu.aria")}
        >
          <div className="campo" style={{ marginBottom: 0 }}>
            <label>{t("quick.provider")}</label>
            <select
              value={props.provider.id}
              aria-label={t("quick.provider")}
              onChange={(evento) => props.onActivar(evento.target.value)}
            >
              {props.providers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {item.model ? ` · ${item.model}` : ""}
                </option>
              ))}
            </select>
          </div>

          <div className="campo" style={{ marginBottom: 0 }}>
            <label>{t("quick.model")}</label>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <select
                value={modeloSel}
                disabled={cargandoModelos}
                aria-label={t("quick.model")}
                onChange={(evento) => setModeloSel(evento.target.value)}
              >
                {modeloSel && !opciones.includes(modeloSel) && (
                  <option value={modeloSel}>
                    {modeloSel}
                    {pendiente ? "" : t("quick.current")}
                  </option>
                )}
                {opciones.length === 0 && !modeloSel && <option value="">{t("quick.noModels")}</option>}
                {opciones.map((item) => (
                  <option key={item} value={item}>
                    {item}
                    {item === modelos?.suggested ? t("common.mostUsed") : ""}
                  </option>
                ))}
              </select>
              <button
                className="quiet"
                onClick={() => setRecarga((valor) => valor + 1)}
                disabled={cargandoModelos}
                title={t("settings.roles.row.refresh.title")}
                aria-label={t("settings.roles.row.refresh.aria")}
              >
                {cargandoModelos ? "…" : "↻"}
              </button>
            </div>
            {sinLista && (
              <span className="nota" style={{ margin: 0 }}>
                {motivoLista}
                {t("quick.noList.note")}
              </span>
            )}
          </div>

          <label
            className="opcion"
            title={
              canalThink
                ? t("editor.think.title", { canal: canalThink })
                : t("editor.think.none")
            }
          >
            <input
              type="checkbox"
              checked={think}
              disabled={!canalThink || guardando}
              onChange={(evento) => cambiarThink(evento.target.checked)}
            />
            Think
          </label>
          {!canalThink && (
            <span className="nota" style={{ margin: 0 }}>
              {t("quick.think.disabled")}
            </span>
          )}

          <div className="campo" style={{ marginBottom: 0 }}>
            <label>{t("quick.effort")}</label>
            <select
              value={esfuerzo}
              disabled={niveles.length === 0 || guardando}
              aria-label={t("quick.effort.aria")}
              title={t("editor.effort.title")}
              onChange={(evento) => cambiarEsfuerzo(evento.target.value)}
            >
              {niveles.map((nivel) => (
                <option key={nivel} value={nivel}>
                  {nivel}
                </option>
              ))}
              {esfuerzo !== "auto" && !niveles.includes(esfuerzo) && (
                <option value={esfuerzo}>{esfuerzo}</option>
              )}
              {niveles.length === 0 && <option value="auto">{t("editor.effort.none")}</option>}
            </select>
          </div>

          {pendiente && (
            <div className="acciones" style={{ margin: 0 }}>
              <button className="primary" onClick={() => void aplicarModelo()} disabled={guardando}>
                {guardando ? t("quick.applying") : t("quick.applyModel", { modelo: modeloSel })}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
