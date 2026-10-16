/**
 * Arranca y controla el backend Python. El token del backend sólo vive en este proceso:
 * el renderer habla siempre por IPC y nunca lo ve.
 */

import { ChildProcessByStdio, spawn } from "node:child_process";
import { createInterface } from "node:readline";
import type { Readable } from "node:stream";

/** El proceso del backend: sin stdin (no se le escribe), con stdout y stderr en tubería. */
type Hijo = ChildProcessByStdio<null, Readable, Readable>;

export interface Handshake {
  port: number;
  token: string;
}

export interface StreamItem {
  index: number;
  event: Record<string, unknown>;
}

export interface ApiFailure {
  code: string;
  message: string;
  status: number;
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const HANDSHAKE_TIMEOUT_MS = 20_000;
const SHUTDOWN_GRACE_MS = 1_500;
const STDERR_TAIL = 40;

export class Backend {
  private child: Hijo | null = null;
  private port = 0;
  private token = "";
  private stderr: string[] = [];

  constructor(
    private readonly repoRoot: string,
    private readonly python: string,
    private readonly home?: string,
  ) {}

  get url(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  get ready(): boolean {
    return this.port > 0 && this.child !== null;
  }

  /** Lanza `python -m satellite.desktop` y espera su línea de handshake. */
  async start(project?: string): Promise<Handshake> {
    if (this.child) return { port: this.port, token: this.token };
    const args = ["-m", "satellite.desktop"];
    if (project) args.push("--project", project);
    if (this.home) args.push("--home", this.home);
    const child = spawn(this.python, args, {
      cwd: this.repoRoot,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    }) as Hijo;
    this.child = child;
    this.stderr = [];
    child.stderr.on("data", (chunk: Buffer) => {
      this.stderr.push(chunk.toString("utf8"));
      if (this.stderr.length > STDERR_TAIL) this.stderr.shift();
    });
    child.on("exit", () => {
      this.child = null;
      this.port = 0;
      this.token = "";
    });
    const handshake = await this.waitHandshake(child);
    this.port = handshake.port;
    this.token = handshake.token;
    return handshake;
  }

  private waitHandshake(child: Hijo): Promise<Handshake> {
    return new Promise((resolve, reject) => {
      const lineas = createInterface({ input: child.stdout });
      const timer = setTimeout(() => {
        lineas.close();
        reject(new Error(`el backend no respondió en ${HANDSHAKE_TIMEOUT_MS / 1000} s${this.tail()}`));
      }, HANDSHAKE_TIMEOUT_MS);
      lineas.on("line", (linea: string) => {
        const texto = linea.trim();
        if (!texto.startsWith("{")) return; // sólo la primera línea JSON es el handshake
        try {
          const data = JSON.parse(texto) as Partial<Handshake>;
          if (typeof data.port === "number" && typeof data.token === "string") {
            clearTimeout(timer);
            lineas.close();
            resolve({ port: data.port, token: data.token });
          }
        } catch {
          // una línea que no es el handshake no se interpreta
        }
      });
      child.on("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`el backend terminó antes de arrancar (código ${code ?? "?"})${this.tail()}`));
      });
    });
  }

  private tail(): string {
    const texto = this.stderr.join("").trim();
    return texto ? `: ${texto.split("\n").slice(-3).join(" ")}` : "";
  }

  /** Petición a la API local, con el token. Lanza `ApiError` con el mensaje del backend. */
  async request<T>(ruta: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
    if (!this.ready) throw new ApiError("backend_down", "el backend no está en marcha", 0);
    const conCuerpo = options.body !== undefined;
    const respuesta = await fetch(`${this.url}/api${ruta}`, {
      method: options.method ?? "GET",
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(conCuerpo ? { "Content-Type": "application/json" } : {}),
      },
      body: conCuerpo ? JSON.stringify(options.body) : undefined,
    });
    const texto = await respuesta.text();
    let data: unknown = {};
    if (texto) {
      try {
        data = JSON.parse(texto);
      } catch {
        data = { error: { code: "bad_response", message: texto.slice(0, 300) } };
      }
    }
    if (!respuesta.ok) {
      const error = (data as { error?: ApiFailure }).error;
      throw new ApiError(
        error?.code ?? "http_error",
        error?.message ?? `error ${respuesta.status}`,
        respuesta.status,
      );
    }
    return data as T;
  }

  /** Consume el SSE y entrega cada evento. Devuelve la función para cortar el flujo. */
  stream(
    after: number,
    onItem: (item: StreamItem) => void,
    onEnd: (mensaje: string) => void,
  ): () => void {
    const controller = new AbortController();
    void (async () => {
      try {
        const respuesta = await fetch(`${this.url}/api/events?after=${after}`, {
          headers: { Authorization: `Bearer ${this.token}`, Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!respuesta.ok || !respuesta.body) {
          onEnd(`el flujo de eventos respondió ${respuesta.status}`);
          return;
        }
        const lector = respuesta.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await lector.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const partes = buffer.split("\n\n");
          buffer = partes.pop() ?? "";
          for (const parte of partes) {
            const datos = parte.split("\n").find((linea) => linea.startsWith("data: "));
            if (!datos) continue; // los latidos son comentarios
            try {
              onItem(JSON.parse(datos.slice(6)) as StreamItem);
            } catch {
              // un frame ilegible no corta el flujo
            }
          }
        }
        onEnd("el flujo de eventos terminó");
      } catch (exc) {
        if (!controller.signal.aborted) onEnd(String(exc));
      }
    })();
    return () => controller.abort();
  }

  /** Cierre ordenado: pide el apagado (drena lo pendiente) y luego termina el proceso. */
  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    try {
      await Promise.race([
        this.request("/shutdown", { method: "POST" }),
        new Promise((resolve) => setTimeout(resolve, SHUTDOWN_GRACE_MS)),
      ]);
    } catch {
      // si ya no responde, se termina igualmente
    }
    child.kill();
    this.child = null;
    this.port = 0;
    this.token = "";
  }
}
