/** Official Sign in with ChatGPT flow for the local desktop app. */

import { app, safeStorage, shell } from "electron";
import { createHash, createPublicKey, randomBytes, randomUUID, verify } from "node:crypto";
import { createServer, type Server } from "node:http";
import { readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";

const ISSUER = "https://auth.openai.com";
const RESOURCE = "https://api.openai.com/v1";
const CLIENT_ID = "dynamic_agent_client";
const CALLBACK_PATH = "/auth/callback";
const TOKEN_URL = `${ISSUER}/api/accounts/oauth/token`;
const JWKS_URL = `${ISSUER}/.well-known/jwks.json`;

interface ChatGPTCredentials {
  email: string;
  subject: string;
  client_id: string;
  ext_agent_host_id: string;
  id_token: string;
  access_token: string;
  refresh_token: string;
  expires_at: number;
  scopes: string[];
}

interface JwtPayload {
  iss?: string;
  aud?: string | string[];
  exp?: number;
  nonce?: string;
  sub?: string;
  email?: string;
  email_verified?: boolean;
}

const b64url = (value: Buffer | string): string =>
  Buffer.from(value).toString("base64url");

function credentialsPath(): string {
  return path.join(app.getPath("userData"), "chatgpt-account.enc");
}

function hostIdPath(): string {
  return path.join(app.getPath("userData"), "chatgpt-host-id");
}

function hostId(): string {
  try {
    return readFileSync(hostIdPath(), "utf8").trim();
  } catch {
    const value = `urn:uuid:${randomUUID()}`;
    writeFileSync(hostIdPath(), value, { mode: 0o600 });
    return value;
  }
}

function loadCredentials(): ChatGPTCredentials | null {
  try {
    if (!storageAvailable()) return null;
    const encrypted = readFileSync(credentialsPath());
    const parsed: unknown = JSON.parse(safeStorage.decryptString(encrypted));
    if (!parsed || typeof parsed !== "object") return null;
    const value = parsed as Partial<ChatGPTCredentials>;
    return typeof value.access_token === "string" && typeof value.refresh_token === "string"
      ? (value as ChatGPTCredentials)
      : null;
  } catch {
    return null;
  }
}

function saveCredentials(value: ChatGPTCredentials): void {
  if (!storageAvailable()) {
    throw new Error("El sistema no ofrece almacenamiento cifrado para las credenciales.");
  }
  const destination = credentialsPath();
  const temporary = `${destination}.tmp`;
  writeFileSync(temporary, safeStorage.encryptString(JSON.stringify(value)), {
    mode: 0o600,
  });
  renameSync(temporary, destination);
}

function storageAvailable(): boolean {
  if (!safeStorage.isEncryptionAvailable()) return false;
  const backend = safeStorage.getSelectedStorageBackend?.();
  return !backend || backend !== "basic_text";
}

function tokenPayload(token: string): JwtPayload {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("OpenAI devolvió un ID token inválido.");
  return JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8")) as JwtPayload;
}

async function validateIdToken(token: string, clientId: string, nonce: string): Promise<JwtPayload> {
  const [encodedHeader, encodedPayload, encodedSignature] = token.split(".");
  if (!encodedHeader || !encodedPayload || !encodedSignature) {
    throw new Error("OpenAI devolvió un ID token inválido.");
  }
  const header = JSON.parse(Buffer.from(encodedHeader, "base64url").toString("utf8")) as {
    alg?: string;
    kid?: string;
  };
  if (header.alg !== "RS256" || !header.kid) throw new Error("Algoritmo de ID token no admitido.");
  const jwksResponse = await fetch(JWKS_URL);
  if (!jwksResponse.ok) throw new Error("No se pudieron validar las credenciales de OpenAI.");
  const jwks = (await jwksResponse.json()) as { keys?: JsonWebKey[] };
  const jwk = jwks.keys?.find((key) => (key as JsonWebKey & { kid?: string }).kid === header.kid);
  if (!jwk) throw new Error("La firma del ID token no coincide con las claves de OpenAI.");
  const signingInput = Buffer.from(`${encodedHeader}.${encodedPayload}`);
  const signature = Buffer.from(encodedSignature, "base64url");
  if (!verify("RSA-SHA256", signingInput, createPublicKey({ key: jwk, format: "jwk" }), signature)) {
    throw new Error("La firma del ID token de OpenAI no es válida.");
  }
  const payload = tokenPayload(token);
  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (
    payload.iss !== ISSUER ||
    !audiences.includes(clientId) ||
    !payload.exp || payload.exp <= Date.now() / 1000 ||
    payload.nonce !== nonce ||
    !payload.sub
  ) {
    throw new Error("OpenAI devolvió una identidad que no se pudo verificar.");
  }
  return payload;
}

async function exchange(form: Record<string, string>): Promise<Record<string, unknown>> {
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(form),
  });
  const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) throw new Error(String(data.error_description || data.error || "Falló el inicio de sesión con OpenAI."));
  return data;
}

function callbackPage(message: string): string {
  const safe = message.replace(/[<>&"']/g, "");
  return `<!doctype html><meta charset="utf-8"><title>Satellite</title><p>${safe}. Puedes volver a Satellite.</p>`;
}

/** Stores one account, performs OAuth, and serves short-lived access tokens only to the local backend. */
export class ChatGPTAccount {
  private broker: Server | null = null;
  private brokerPort = 0;
  private brokerSecret = "";
  private pendingLogin: Promise<{ email: string }> | null = null;
  private pendingRefresh: Promise<string> | null = null;

  status(): { connected: boolean; email: string } {
    const stored = loadCredentials();
    return { connected: Boolean(stored), email: stored?.email ?? "" };
  }

  disconnect(): void {
    try {
      unlinkSync(credentialsPath());
    } catch {
      // Missing credentials are already disconnected.
    }
  }

  signIn(): Promise<{ email: string }> {
    if (this.pendingLogin) return this.pendingLogin;
    this.pendingLogin = this.runSignIn().finally(() => {
      this.pendingLogin = null;
    });
    return this.pendingLogin;
  }

  private async runSignIn(): Promise<{ email: string }> {
    const previous = loadCredentials();
    const requestedClientId = previous?.client_id || CLIENT_ID;
    const state = b64url(randomBytes(32));
    const nonce = b64url(randomBytes(32));
    const verifier = b64url(randomBytes(48));
    const challenge = createHash("sha256").update(verifier).digest("base64url");
    const host = hostId();
    const server = createServer();
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("No se pudo abrir el callback local.");
    const redirectUri = `http://127.0.0.1:${address.port}${CALLBACK_PATH}`;

    let callbackTimeout: ReturnType<typeof setTimeout> | undefined;
    const callback = new Promise<{ code: string; clientId: string }>((resolve, reject) => {
      callbackTimeout = setTimeout(() => reject(new Error("Se agotó el tiempo de inicio de sesión.")), 5 * 60_000);
      server.on("request", (request, response) => {
        const url = new URL(request.url ?? "/", redirectUri);
        if (url.pathname !== CALLBACK_PATH) {
          response.writeHead(404).end();
          return;
        }
        if (url.searchParams.get("state") !== state) {
          response.writeHead(400).end(callbackPage("No se pudo validar esta sesión"));
          clearTimeout(callbackTimeout);
          reject(new Error("El estado de autorización de OpenAI no coincide."));
          return;
        }
        const authError = url.searchParams.get("error");
        if (authError) {
          response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
            .end(callbackPage("Autorización cancelada"));
          clearTimeout(callbackTimeout);
          reject(new Error(authError === "access_denied" ? "Se canceló el inicio de sesión con ChatGPT." : authError));
          return;
        }
        const code = url.searchParams.get("code") ?? "";
        const returnedClientId = url.searchParams.get("client_id") ?? "";
        const issuedClientId = returnedClientId || (previous ? requestedClientId : "");
        if (!code || !issuedClientId || (previous && issuedClientId !== requestedClientId)) {
          response.writeHead(400, { "Content-Type": "text/html; charset=utf-8" })
            .end(callbackPage("No se completó el registro de Satellite"));
          clearTimeout(callbackTimeout);
          reject(new Error("OpenAI no devolvió el código o el identificador de la aplicación."));
          return;
        }
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
          .end(callbackPage("Inicio de sesión completado"));
        clearTimeout(callbackTimeout);
        resolve({ code, clientId: issuedClientId });
      });
    });
    // If opening the system browser fails, the callback is never awaited; still observe its
    // timeout rejection while preserving the rejection for the normal `await callback` path.
    void callback.catch(() => {});

    try {
      const url = new URL(`${ISSUER}/api/accounts/authorize`);
      const query: Record<string, string> = {
        client_id: requestedClientId,
        ext_agent_host_id: host,
        response_type: "code",
        redirect_uri: redirectUri,
        scope: "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
        resource: RESOURCE,
        state,
        nonce,
        code_challenge_method: "S256",
        code_challenge: challenge,
      };
      if (!previous) query.agent_name_hint = "Satellite";
      if (previous?.id_token) query.id_token_hint = previous.id_token;
      if (previous?.email) query.login_hint = previous.email;
      for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
      await shell.openExternal(url.toString());
      const result = await callback;
      const tokens = await exchange({
        grant_type: "authorization_code",
        client_id: result.clientId,
        code: result.code,
        code_verifier: verifier,
        redirect_uri: redirectUri,
        resource: RESOURCE,
      });
      const accessToken = String(tokens.access_token ?? "");
      const refreshToken = String(tokens.refresh_token ?? "");
      const idToken = String(tokens.id_token ?? "");
      const scopes = String(tokens.scope ?? "").split(/\s+/).filter(Boolean);
      if (!accessToken || !refreshToken || !idToken || !scopes.includes("chatgpt.tokens.use.direct")) {
        throw new Error("La cuenta no autorizó el uso de modelos del plan de ChatGPT.");
      }
      const identity = await validateIdToken(idToken, result.clientId, nonce);
      saveCredentials({
        email: String(identity.email ?? ""),
        subject: String(identity.sub),
        client_id: result.clientId,
        ext_agent_host_id: host,
        id_token: idToken,
        access_token: accessToken,
        refresh_token: refreshToken,
        expires_at: Date.now() + Number(tokens.expires_in ?? 3600) * 1000,
        scopes,
      });
      return { email: String(identity.email ?? "") };
    } finally {
      if (callbackTimeout) clearTimeout(callbackTimeout);
      server.close();
    }
  }

  /** Starts a loopback token broker consumed by the Python runtime, never by the renderer. */
  async startBroker(): Promise<{ url: string; secret: string }> {
    if (this.broker) return { url: `http://127.0.0.1:${this.brokerPort}`, secret: this.brokerSecret };
    this.brokerSecret = b64url(randomBytes(32));
    this.broker = createServer((request, response) => {
      if (request.headers.authorization !== `Bearer ${this.brokerSecret}`) {
        response.writeHead(401).end();
        return;
      }
      if (request.method !== "GET" || request.url !== "/chatgpt/token") {
        response.writeHead(404).end();
        return;
      }
      void this.accessToken().then((token) => {
        response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
        response.end(JSON.stringify({ access_token: token }));
      }).catch((error: unknown) => {
        response.writeHead(401, { "Content-Type": "application/json", "Cache-Control": "no-store" });
        response.end(JSON.stringify({ error: String(error) }));
      });
    });
    await new Promise<void>((resolve, reject) => {
      this.broker?.once("error", reject);
      this.broker?.listen(0, "127.0.0.1", () => resolve());
    });
    const address = this.broker.address();
    if (!address || typeof address === "string") throw new Error("No se pudo iniciar el servicio local de credenciales.");
    this.brokerPort = address.port;
    return { url: `http://127.0.0.1:${this.brokerPort}`, secret: this.brokerSecret };
  }

  private async accessToken(): Promise<string> {
    const current = loadCredentials();
    if (!current) throw new Error("Conecta primero tu cuenta de ChatGPT en Ajustes.");
    if (current.expires_at > Date.now() + 60_000) return current.access_token;
    if (this.pendingRefresh) return this.pendingRefresh;
    this.pendingRefresh = this.refreshAccessToken(current).finally(() => {
      this.pendingRefresh = null;
    });
    return this.pendingRefresh;
  }

  private async refreshAccessToken(current: ChatGPTCredentials): Promise<string> {
    const refreshed = await exchange({
      grant_type: "refresh_token",
      client_id: current.client_id,
      refresh_token: current.refresh_token,
      resource: RESOURCE,
    });
    const accessToken = String(refreshed.access_token ?? "");
    if (!accessToken) throw new Error("No se pudo renovar la conexión con ChatGPT. Vuelve a iniciar sesión.");
    const next: ChatGPTCredentials = {
      ...current,
      access_token: accessToken,
      refresh_token: String(refreshed.refresh_token || current.refresh_token),
      expires_at: Date.now() + Number(refreshed.expires_in ?? 3600) * 1000,
    };
    saveCredentials(next);
    return next.access_token;
  }
}

type JsonWebKey = import("node:crypto").JsonWebKey;
