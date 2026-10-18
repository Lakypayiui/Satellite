/**
 * Barra lateral: proyecto abierto, conversaciones ordenadas por actividad y accesos
 * secundarios (recientes, archivo y proveedor). Compacta y sin adornos. El texto de interfaz
 * sale del i18n tipado (`useT`).
 */

import { useMemo, useState } from "react";

import { SavedProviderView, SessionInfo } from "./api";
import { useT } from "./i18n";

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
  onFijar: (id: string, pinned: boolean) => void;
  onArchivar: (id: string, archived: boolean) => void;
  onAjustes: () => void;
}

function corto(ruta: string): string {
  return ruta.split(/[\\/]/).filter(Boolean).at(-1) ?? ruta;
}

function porOrdenPrincipal(sesiones: SessionInfo[]): SessionInfo[] {
  return sesiones
    .filter((sesion) => !sesion.archived)
    .sort((a, b) => {
      if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1;
      return (b.updated_at || "").localeCompare(a.updated_at || "");
    });
}

function porActividad(sesiones: SessionInfo[]): SessionInfo[] {
  return [...sesiones].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}

function cuando(iso: string): string {
  if (!iso) return "";
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return "";
  const hoy = new Date();
  return fecha.toDateString() === hoy.toDateString()
    ? fecha.toLocaleTimeString().slice(0, 5)
    : fecha.toLocaleDateString();
}

export function Sidebar(props: Props) {
  const t = useT();
  const [renombrando, setRenombrando] = useState("");
  const [nombre, setNombre] = useState("");
  const [menu, setMenu] = useState("");
  const activas = useMemo(() => porOrdenPrincipal(props.sesiones), [props.sesiones]);
  const archivadas = useMemo(
    () => porActividad(props.sesiones.filter((sesion) => sesion.archived)),
    [props.sesiones],
  );

  if (props.plegado) {
    return (
      <aside className="panel izq plegado">
        <button
          className="plegar"
          onClick={props.onPlegar}
          title={t("sidebar.showPanel")}
          aria-label={t("sidebar.showPanel.aria")}
        >
          ›
        </button>
        <button
          className="plegar"
          onClick={props.onAjustes}
          title={t("sidebar.settings.title")}
          aria-label={t("sidebar.settings.aria")}
          style={{ marginTop: "auto", fontSize: "calc(var(--fs-titulo) * 1.4)" }}
        >
          ⚙
        </button>
      </aside>
    );
  }

  const otros = props.recientes.filter((ruta) => ruta !== props.ruta);

  return (
    <aside className="panel izq">
      <button
        className="plegar"
        onClick={props.onPlegar}
        title={t("sidebar.hidePanel")}
        aria-label={t("sidebar.hidePanel.aria")}
      >
        ‹
      </button>

      <div className="bloque">
        <p className="etiqueta">{t("sidebar.project")}</p>
        <div className="item" style={{ cursor: "default" }} title={props.ruta}>
          <span className="nombre">{corto(props.ruta) || t("sidebar.noProject")}</span>
        </div>
        <div className="meta" style={{ display: "block", overflowWrap: "anywhere" }}>
          {props.ruta || "—"}
        </div>
        <div className="acciones">
          <button
            className="quiet"
            onClick={() => props.onAbrir()}
            title={t("sidebar.openFolder.title")}
          >
            {t("sidebar.changeFolder")}
          </button>
        </div>
      </div>

      <div className="bloque">
        <p className="etiqueta">{t("sidebar.conversations")}</p>
        <button
          className="nueva-conv"
          onClick={props.onNueva}
          title={t("sidebar.newChat.title")}
        >
          <span className="signo" aria-hidden="true">
            +
          </span>
          {t("sidebar.newChat")}
        </button>
      </div>

      <div className="lista" aria-label={t("sidebar.conversations.aria")}>
        {activas.length === 0 && <div className="vacio">{t("sidebar.empty")}</div>}
        {activas.map((sesion) => {
          const etiqueta = sesion.name || sesion.id;
          if (renombrando === sesion.id) {
            return (
              <div key={sesion.id} className="item">
                <input
                  autoFocus
                  value={nombre}
                  aria-label={t("sidebar.rename.aria")}
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
            );
          }
          return (
            <div key={sesion.id} className="conv">
              <div
                className={
                  "item" +
                  (sesion.id === props.activa ? " activo" : "") +
                  (sesion.pinned ? " fijada" : "")
                }
                onClick={() => {
                  setMenu("");
                  props.onUsar(sesion.id);
                }}
                onDoubleClick={() => {
                  setRenombrando(sesion.id);
                  setNombre(sesion.name);
                }}
                title={t("sidebar.rename.hint")}
              >
                {sesion.pinned && (
                  <span className="pin" title={t("sidebar.pinned")} aria-label={t("sidebar.pinned")}>
                    ★
                  </span>
                )}
                <span className="nombre">{etiqueta}</span>
                <span className="meta" title={t("sidebar.turns", { n: sesion.turns })}>
                  {sesion.turns}
                  {cuando(sesion.updated_at) ? ` · ${cuando(sesion.updated_at)}` : ""}
                </span>
                <button
                  className="accion-item"
                  aria-label={t("sidebar.actionsOf", { nombre: etiqueta })}
                  aria-expanded={menu === sesion.id}
                  title={t("sidebar.actions")}
                  onClick={(evento) => {
                    evento.stopPropagation();
                    setMenu(menu === sesion.id ? "" : sesion.id);
                  }}
                >
                  ⋯
                </button>
              </div>
              {menu === sesion.id && (
                <div
                  className="menu-item"
                  role="group"
                  aria-label={t("sidebar.actionsOf", { nombre: etiqueta })}
                >
                  <button
                    onClick={(evento) => {
                      evento.stopPropagation();
                      props.onFijar(sesion.id, !sesion.pinned);
                      setMenu("");
                    }}
                  >
                    {sesion.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
                  </button>
                  <button
                    onClick={(evento) => {
                      evento.stopPropagation();
                      props.onArchivar(sesion.id, true);
                      setMenu("");
                    }}
                  >
                    {t("sidebar.archive")}
                  </button>
                  <button
                    onClick={(evento) => {
                      evento.stopPropagation();
                      setRenombrando(sesion.id);
                      setNombre(sesion.name);
                      setMenu("");
                    }}
                  >
                    {t("sidebar.rename")}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {archivadas.length > 0 && (
        <details className="bloque archivadas">
          <summary className="etiqueta">{t("sidebar.archived", { n: archivadas.length })}</summary>
          <p className="nota" style={{ marginTop: 0 }}>
            {t("sidebar.archived.note")}
          </p>
          {archivadas.map((sesion) => {
            const etiqueta = sesion.name || sesion.id;
            return (
              <div key={sesion.id} className="conv">
                <div className="item archivada" title={etiqueta}>
                  {sesion.pinned && (
                    <span
                      className="pin"
                      title={t("sidebar.pinned")}
                      aria-label={t("sidebar.pinned")}
                    >
                      ★
                    </span>
                  )}
                  <span className="nombre">{etiqueta}</span>
                  <span className="meta">{sesion.turns}</span>
                  <button
                    className="accion-item siempre"
                    aria-label={t("sidebar.actionsOf", { nombre: etiqueta })}
                    aria-expanded={menu === sesion.id}
                    title={t("sidebar.actions")}
                    onClick={() => setMenu(menu === sesion.id ? "" : sesion.id)}
                  >
                    ⋯
                  </button>
                </div>
                {menu === sesion.id && (
                  <div
                    className="menu-item"
                    role="group"
                    aria-label={t("sidebar.actionsOf", { nombre: etiqueta })}
                  >
                    <button
                      onClick={() => {
                        props.onFijar(sesion.id, !sesion.pinned);
                        setMenu("");
                      }}
                    >
                      {sesion.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
                    </button>
                    <button
                      onClick={() => {
                        props.onArchivar(sesion.id, false);
                        setMenu("");
                      }}
                    >
                      {t("sidebar.unarchive")}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </details>
      )}

      {otros.length > 0 && (
        <details className="bloque recientes">
          <summary className="etiqueta">{t("sidebar.recent", { n: otros.length })}</summary>
          {otros.slice(0, 5).map((ruta) => (
            <div key={ruta} className="item" onClick={() => props.onAbrir(ruta)} title={ruta}>
              <span className="nombre">{corto(ruta)}</span>
            </div>
          ))}
        </details>
      )}

      <div className="bloque pie-panel">
        <p className="etiqueta">{t("sidebar.provider")}</p>
        <div className="item" style={{ cursor: "default" }}>
          <span className="nombre">
            {props.proveedor ? props.proveedor.name : t("chip.model.none")}
          </span>
          {props.proveedor?.model && <span className="meta">{props.proveedor.model}</span>}
        </div>
        {props.proveedor?.api_key_set === false && <div className="meta">{t("sidebar.noKey")}</div>}
        <button
          className="quiet ajustes-pie"
          onClick={props.onAjustes}
          title={t("sidebar.settings.title")}
          aria-label={t("sidebar.settings.aria")}
        >
          <span className="glifo" aria-hidden="true">
            ⚙
          </span>
          {t("settings.title")}
        </button>
      </div>
    </aside>
  );
}
