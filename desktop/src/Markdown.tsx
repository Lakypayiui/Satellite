/**
 * Render de Markdown del chat. Sólo se usa en turnos del agente: el HTML crudo
 * se ignora, los enlaces se abren fuera de la app y las imágenes remotas no se
 * cargan (el texto del modelo es dato no confiable).
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { bridge } from "./api";

export function Markdown({ texto }: { texto: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              onClick={(evento) => {
                evento.preventDefault();
                if (href) void bridge.abrirExterno(href);
              }}
            >
              {children}
            </a>
          ),
          img: ({ alt }) => (
            <span className="md-imagen-omitida">[imagen: {alt || "sin texto"}]</span>
          ),
        }}
      >
        {texto}
      </ReactMarkdown>
    </div>
  );
}
