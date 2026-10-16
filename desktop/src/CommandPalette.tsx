/**
 * Paleta de comandos (Ctrl+K): aquí vive todo lo que en la TUI son los comandos
 * `/doctor`, `/setup`, `/stats`, `/curate`, `/reset`…, sin llenar la pantalla de botones.
 */

import { useEffect, useMemo, useRef, useState } from "react";

export interface Comando {
  clave: string;
  texto: string;
  /** Si se pide argumento, la paleta lo pide antes de ejecutar. */
  pide?: string;
}

export const COMANDOS: Comando[] = [
  { clave: "proyecto", texto: "Abrir otra carpeta de proyecto" },
  { clave: "sesion-nueva", texto: "Nueva conversación" },
  { clave: "proveedor", texto: "Proveedor y modelo (URL y cargar modelos)" },
  { clave: "roles", texto: "Roles: orchestrator, context, agents, creator" },
  { clave: "politica", texto: "Política de seguridad y modo seguro" },
  { clave: "sandbox", texto: "Detectar Docker y registrar la decisión (setup)" },
  { clave: "doctor", texto: "Diagnóstico del proyecto (doctor)" },
  { clave: "stats", texto: "Métricas de skills y caché (stats)" },
  { clave: "compactar", texto: "Compactar el contexto de la conversación" },
  { clave: "snapshots", texto: "Snapshots y restaurar uno" },
  { clave: "reset", texto: "Reset: borrar agentes y skills creados" },
  { clave: "config", texto: "Ver la configuración del proyecto" },
  { clave: "agentes", texto: "Ver los agentes que participaron" },
  { clave: "crear-agente", texto: "Crear un agente para una necesidad", pide: "¿Qué capacidad hace falta?" },
  { clave: "skills-buscar", texto: "Buscar en las skills", pide: "Texto a buscar" },
  { clave: "skills-borrar", texto: "Borrar una skill", pide: "Id de la skill" },
  { clave: "curar", texto: "Ejecutar el curador de skills", pide: "Petición (opcional)" },
  { clave: "cambios", texto: "Cambios y deshacer de una tarea" },
  { clave: "trabajos", texto: "Comandos en segundo plano" },
  { clave: "parar-trabajo", texto: "Detener un trabajo", pide: "Id del trabajo" },
  { clave: "paneles", texto: "Mostrar u ocultar los paneles laterales" },
  { clave: "atajos", texto: "Atajos de teclado" },
  { clave: "salir", texto: "Cerrar la app" },
];

interface Props {
  onEjecutar: (clave: string, argumento: string) => void;
  onCerrar: () => void;
}

export function CommandPalette(props: Props) {
  const [filtro, setFiltro] = useState("");
  const [pendiente, setPendiente] = useState<Comando | null>(null);
  const [elegido, setElegido] = useState(0);
  const entrada = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    entrada.current?.focus();
  }, []);

  const visibles = useMemo(() => {
    if (pendiente) return [pendiente];
    const texto = filtro.trim().toLowerCase();
    if (!texto) return COMANDOS;
    return COMANDOS.filter(
      (comando) =>
        comando.clave.includes(texto) ||
        comando.texto.toLowerCase().includes(texto) ||
        comando.pide?.toLowerCase().includes(texto),
    );
  }, [filtro, pendiente]);

  function confirmar() {
    const comando = visibles[Math.min(elegido, visibles.length - 1)];
    if (!comando) return;
    if (pendiente) {
      props.onEjecutar(pendiente.clave, filtro.trim());
      props.onCerrar();
      return;
    }
    if (comando.pide) {
      setPendiente(comando);
      setFiltro("");
      setElegido(0);
      return;
    }
    props.onEjecutar(comando.clave, "");
    props.onCerrar();
  }

  return (
    <div className="paleta" onClick={props.onCerrar}>
      <div className="hoja" onClick={(evento) => evento.stopPropagation()}>
        <input
          ref={entrada}
          value={filtro}
          placeholder={pendiente ? pendiente.pide : "Comando…"}
          onChange={(evento) => {
            setFiltro(evento.target.value);
            setElegido(0);
          }}
          onKeyDown={(evento) => {
            if (evento.key === "Escape") {
              if (pendiente) {
                setPendiente(null);
                setFiltro("");
              } else {
                props.onCerrar();
              }
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
        <div className="resultados">
          {visibles.length === 0 && <div className="vacio">Sin coincidencias.</div>}
          {!pendiente &&
            visibles.map((comando, indice) => (
              <div
                key={comando.clave}
                className={indice === elegido ? "acceso elegido" : "acceso"}
                onMouseEnter={() => setElegido(indice)}
                onClick={confirmar}
              >
                <span className="clave">/{comando.clave}</span>
                <span className="texto">{comando.texto}</span>
              </div>
            ))}
        </div>
        <div className="pie">
          ↑↓ elegir · Enter ejecutar · Esc cerrar · las acciones destructivas piden confirmación
        </div>
      </div>
    </div>
  );
}
