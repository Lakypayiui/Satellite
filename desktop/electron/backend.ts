/**
 * Arranca y controla el backend Python. El token lo genera este proceso y lo comparte
 * con el backend por stdin; el renderer habla siempre por IPC y nunca lo ve.
 */

import { randomBytes } from "node:crypto";
import { type ChildProcessByStdio, spawn } from "node:child_process";
import { createInterface } from "node:readline";
import type { Readable, Writable } from "node:stream";

/** El proceso del backend: stdin para el token, stdout/stderr en tubería. */
type Hijo = ChildProcessByStdio<Writable, Readable, Readable>;

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

/** Cómo se lanza el backend: el ejecutable empaquetado o ``python -m`` en desarrollo. */
export interface BackendLaunch {
  command: string;
  args: string[];
  cwd: string;
}

export class Backend {
  private child: Hijo | null = null;
  private port = 0;
  private token = "";
  private stderr: string[] = [];

  constructor(
    private readonly launch: BackendLaunch,
    private readonly home?: string,
    private readonly accountBroker?: { url: string; secret: string },
  ) {}

  get url(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  get ready(): boolean {
    return this.port > 0 && this.child !== null;
  }

  /** Lanza el backend (``satellite desktop-backend`` o ``python -m satellite.desktop``)
   * y espera su línea de handshake. */
  async start(project?: string): Promise<Handshake> {
    if (this.child) return { port: this.port, token: this.token };
    // El secreto lo genera este proceso y viaja por stdin: no aparece en argv, ni en
    // stdout, ni en los logs del backend. Python sólo anuncia el puerto.
    const token = randomBytes(32).toString("hex");
    const args = [...this.launch.args, "--token-stdin"];
    if (project) args.push("--project", project);
    if (this.home) args.push("--home", this.home);
    const child = spawn(this.launch.command, args, {
      cwd: this.launch.cwd,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        ...(this.accountBroker
          ? {
              SATELLITE_ACCOUNT_BROKER_URL: this.accountBroker.url,
              SATELLITE_ACCOUNT_BROKER_SECRET: this.accountBroker.secret,
            }
          : {}),
      },
      stdio: ["pipe", "pipe", "pipe"],
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
    // Se registra antes de escribir: si el hijo falla al arrancar, la promesa ya
    // está escuchando su salida y no se queda esperando el timeout.
    const handshake = this.waitHandshake(child);
    child.stdin.on("error", () => {
      // Si el hijo sale antes de leerlo, waitHandshake reporta el fallo real.
    });
    child.stdin.end(`${token}\n`);
    let port: number;
    try {
      port = await handshake;
    } catch (exc) {
      this.child = null;
      this.port = 0;
      this.token = "";
      child.kill();
      throw exc;
    }
    this.port = port;
    this.token = token;
    child.stdout.resume(); // drena cualquier log posterior del backend
    return { port, token };
  }

  private waitHandshake(child: Hijo): Promise<number> {
    return new Promise((resolve, reject) => {
      const lineas = createInterface({ input: child.stdout });
      let terminado = false;
      let timer: ReturnType<typeof setTimeout> | undefined;

      const terminar = (accion: () => void) => {
        if (terminado) return;
        terminado = true;
        if (timer) clearTimeout(timer);
        lineas.close();
        child.off("exit", alSalir);
        child.off("error", alError);
        accion();
      };
      const alSalir = (code: number | null) => {
        terminar(() =>
          reject(
            new Error(`el backend terminó antes de arrancar (código ${code ?? "?"})${this.tail()}`),
          ),
        );
      };
      const alError = (exc: Error) => {
        terminar(() => reject(new Error(`no se pudo arrancar el backend: ${exc.message}`)));
      };

      timer = setTimeout(() => {
        terminar(() =>
          reject(new Error(`el backend no respondió en ${HANDSHAKE_TIMEOUT_MS / 1000} s${this.tail()}`)),
        );
      }, HANDSHAKE_TIMEOUT_MS);

      lineas.on("line", (linea: string) => {
        const texto = linea.trim();
        if (!texto.startsWith("{")) return; // sólo la primera línea JSON es el handshake
        try {
          const data = JSON.parse(texto) as { port?: unknown };
          const port = data.port;
          if (
            typeof port === "number" &&
            Number.isInteger(port) &&
            port > 0 &&
            port <= 65_535
          ) {
            terminar(() => resolve(port));
          }
        } catch {
          // una línea que no es el handshake no se interpreta
        }
      });
      child.once("exit", alSalir);
      child.once("error", alError);
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
