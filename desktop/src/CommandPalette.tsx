/**
 * Paleta de comandos (Ctrl+K): aquí vive todo lo que en la TUI son los comandos
 * `/doctor`, `/setup`, `/stats`, `/curate`, `/reset`…, sin llenar la pantalla de botones.
 *
 * La lista NO se escribe aquí: se pide a `GET /commands`, el catálogo compartido por la
 * TUI, la CLI y esta app (una sola definición). Los resultados salen agrupados por
 * categoría, la búsqueda mira id, resumen y etiquetas de campos, y los comandos que
 * necesitan datos los recogen campo a campo antes de ejecutarse. Los que exigen runtime
 * se marcan como tales y se atenúan cuando no está listo.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { CommandSpecView, api, mensajeDe } from "./api";
import { comandoTraducido } from "./commandsI18n";
import { useT } from "./i18n";

interface Props {
  onEjecutar: (clave: string, args: Record<string, string>) => void;
  onCerrar: () => void;
  /** Si el runtime está listo: los comandos que lo necesitan se atenúan si no lo está. */
  runtimeListo: boolean;
  /** Id con el que abrir ya en el paso de argumentos (p. ej. elegido con «/» inline). */
  inicial?: string | null;
  /** Comandos que abren una pantalla de configuración: se ejecutan sin pedir campos, porque el
   *  formulario es esa pantalla (antes se abría aquí una ventana con los campos). */
  paginas?: string[];
}

export function CommandPalette(props: Props) {
  const t = useT();
  const [catalogo, setCatalogo] = useState<CommandSpecView[] | null>(null);
  const [errorCatalogo, setErrorCatalogo] = useState("");
  const [filtro, setFiltro] = useState("");
  const [pendienteId, setPendienteId] = useState<string | null>(null);
  const [valores, setValores] = useState<Record<string, string>>({});
  const [aviso, setAviso] = useState("");
  const [elegido, setElegido] = useState(0);
  const entrada = useRef<HTMLInputElement | null>(null);
  const lista = useRef<HTMLDivElement | null>(null);
  const camposRef = useRef<Array<HTMLInputElement | null>>([]);

  // El catálogo llega del backend en español (`GET /commands`): se traduce **antes** de buscar,
  // agrupar y pintar, para que tanto la búsqueda como las cabeceras de grupo usen el idioma actual.
  // El id y el `usage` no se traducen; los campos van con su etiqueta y su marcador traducidos.
  const comandos = useMemo(
    () => (catalogo ?? []).map((comando) => comandoTraducido(comando, t)),
    [catalogo, t],
  );

  // El comando en curso se resuelve sobre la lista ya traducida (mismo id, texto al día).
  const pendiente = useMemo(
    () => (pendienteId ? comandos.find((comando) => comando.id === pendienteId) ?? null : null),
    [comandos, pendienteId],
  );

  // El catálogo es del backend: una sola definición para las tres interfaces.
  useEffect(() => {
    void (async () => {
      try {
        const datos = await api<{ commands: CommandSpecView[] }>("/commands");
        const todos = Array.isArray(datos?.commands) ? datos.commands : [];
        // El catálogo es de las tres interfaces: aquí sólo se listan las de escritorio.
        const propios = todos.filter((comando) => comando.surfaces.includes("desktop"));
        setCatalogo(propios);
        // Si venimos de un «/comando» inline que pide datos, se abre directamente en sus campos.
        const arranque = props.inicial
          ? propios.find((comando) => comando.id === props.inicial)
          : undefined;
        if (arranque && arranque.args.length > 0) {
          setPendienteId(arranque.id);
          setValores({});
          setFiltro("");
          setAviso("");
          setElegido(0);
        }
      } catch (exc) {
        setCatalogo([]);
        setErrorCatalogo(mensajeDe(exc));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // El foco sigue a la paleta: al recoger datos va al primer campo y al volver, a la búsqueda.
  useEffect(() => {
    if (pendiente) camposRef.current[0]?.focus();
    else entrada.current?.focus();
  }, [pendiente]);

  const visibles = useMemo(() => {
    const texto = filtro.trim().toLowerCase();
    if (!texto) return comandos;
    return comandos.filter(
      (comando) =>
        comando.id.toLowerCase().includes(texto) ||
        comando.summary.toLowerCase().includes(texto) ||
        comando.category.toLowerCase().includes(texto) ||
        comando.args.some(
          (campo) =>
            campo.label.toLowerCase().includes(texto) || campo.key.toLowerCase().includes(texto),
        ),
    );
  }, [comandos, filtro]);

  // Agrupados por categoría, manteniendo el orden del catálogo.
  const grupos = useMemo(() => {
    const mapa = new Map<string, { comando: CommandSpecView; indice: number }[]>();
    visibles.forEach((comando, indice) => {
      const fila = mapa.get(comando.category) ?? [];
      fila.push({ comando, indice });
      mapa.set(comando.category, fila);
    });
    return [...mapa.entries()];
  }, [visibles]);

  // La selección siempre a la vista, también con el teclado.
  useEffect(() => {
    lista.current?.querySelector(".acceso.elegido")?.scrollIntoView({ block: "nearest" });
  }, [elegido, filtro]);

  function elegir(comando: CommandSpecView) {
    if (comando.args.length > 0 && !props.paginas?.includes(comando.id)) {
      setPendienteId(comando.id);
      setValores({});
      setFiltro("");
      setAviso("");
      setElegido(0);
      return;
    }
    props.onEjecutar(comando.id, {});
    props.onCerrar();
  }

  function confirmar() {
    const comando = visibles[Math.min(elegido, visibles.length - 1)];
    if (!comando) return;
    elegir(comando);
  }

  function ejecutar() {
    if (!pendiente) return;
    const campos = pendiente.args;
    const faltan = campos.filter((campo) => !campo.optional && !(valores[campo.key] ?? "").trim());
    if (faltan.length > 0) {
      setAviso(t("palette.missing", { campos: faltan.map((campo) => campo.label).join(", ") }));
      return;
    }
    const salida: Record<string, string> = {};
    for (const campo of campos) salida[campo.key] = (valores[campo.key] ?? "").trim();
    props.onEjecutar(pendiente.id, salida);
    props.onCerrar();
  }

  function volver() {
    setPendienteId(null);
    setValores({});
    setAviso("");
    setFiltro("");
  }

  /** Enter avanza por los campos y ejecuta en el último; nunca deja uno obligatorio vacío. */
  function avanzar(indice: number) {
    const campos = pendiente?.args ?? [];
    const siguiente = campos[indice + 1];
    if (siguiente) {
      camposRef.current[indice + 1]?.focus();
      return;
    }
    const vacio = campos.findIndex((campo) => !campo.optional && !(valores[campo.key] ?? "").trim());
    if (vacio >= 0) {
      camposRef.current[vacio]?.focus();
      setAviso(t("palette.missing", { campos: campos[vacio].label }));
      return;
    }
    ejecutar();
  }

  const campos = pendiente?.args ?? [];

  return (
    <div className="paleta" onClick={props.onCerrar} role="dialog" aria-modal="true" aria-label={t("palette.title")}>
      <div className="hoja" onClick={(evento) => evento.stopPropagation()}>
        {pendiente ? (
          <>
            <div className="cabecera-hoja">
              <h2>{pendiente.summary}</h2>
              <span className="clave">/{pendiente.id}</span>
            </div>
            <div className="campos">
              {campos.map((campo, indice) => (
                <label key={campo.key} className="campo">
                  {campo.label}
                  {campo.optional ? t("palette.optional") : ""}
                  <input
                    ref={(nodo) => {
                      camposRef.current[indice] = nodo;
                    }}
                    value={valores[campo.key] ?? ""}
                    placeholder={campo.placeholder || campo.label}
                    onChange={(evento) => {
                      setValores((previos) => ({ ...previos, [campo.key]: evento.target.value }));
                      setAviso("");
                    }}
                    onKeyDown={(evento) => {
                      if (evento.key === "Enter") {
                        evento.preventDefault();
                        avanzar(indice);
                      }
                      if (evento.key === "Escape") {
                        evento.preventDefault();
                        volver();
                      }
                    }}
                  />
                </label>
              ))}
            </div>
          </>
        ) : (
          <>
            <input
              ref={entrada}
              value={filtro}
              placeholder={t("palette.search.placeholder")}
              aria-label={t("palette.search.aria")}
              onChange={(evento) => {
                setFiltro(evento.target.value);
                setElegido(0);
              }}
              onKeyDown={(evento) => {
                if (evento.key === "Escape") {
                  evento.preventDefault();
                  props.onCerrar();
                }
                if (evento.key === "ArrowDown") {
                  evento.preventDefault();
                  setElegido((valor) => Math.min(valor + 1, visibles.length - 1));
                }
                if (evento.key === "ArrowUp") {
                  evento.preventDefault();
                  setElegido((valor) => Math.max(valor - 1, 0));
                }
                if (evento.key === "Enter") {
                  evento.preventDefault();
                  confirmar();
                }
              }}
            />
            <div className="resultados" ref={lista}>
              {errorCatalogo && (
                <div className="vacio">
                  {t("palette.catalogError")} {errorCatalogo}
                </div>
              )}
              {!errorCatalogo && catalogo === null && (
                <div className="vacio">{t("palette.loading")}</div>
              )}
              {!errorCatalogo && catalogo !== null && visibles.length === 0 && (
                <div className="vacio">{t("palette.noMatches")}</div>
              )}
              {grupos.map(([categoria, filas]) => (
                <div key={categoria}>
                  <div className="grupo">{categoria}</div>
                  {filas.map(({ comando, indice }) => (
                    <div
                      key={comando.id}
                      className={
                        (indice === elegido ? "acceso elegido" : "acceso") +
                        (comando.needs_runtime && !props.runtimeListo ? " no-disponible" : "")
                      }
                      onMouseEnter={() => setElegido(indice)}
                      onClick={() => elegir(comando)}
                    >
                      <span className="clave">/{comando.id}</span>
                      <span className="texto">{comando.summary}</span>
                      {comando.needs_runtime && !props.runtimeListo && (
                        <span className="meta">{t("palette.needsRuntime")}</span>
                      )}
                      {comando.terminal_only && (
                        <span className="meta terminal">{t("palette.terminalOnly")}</span>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </>
        )}

        {aviso && <div className="linea-aviso mal">{aviso}</div>}

        <div className="pie">
          {pendiente ? (
            <>
              <button className="primary" onClick={ejecutar}>
                {t("palette.run")}
              </button>
              <button className="quiet" onClick={volver}>
                {t("common.back")}
              </button>
              <span>{t("palette.footer.form")}</span>
            </>
          ) : (
            t("palette.footer.list")
          )}
        </div>
      </div>
    </div>
  );
}
