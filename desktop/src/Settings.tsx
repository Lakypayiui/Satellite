/**
 * Ajustes: página principal con categorías navegables. Cada categoría es una **página propia**
 * (no un ancla dentro de una página larga): Modelos y proveedores, Roles, Permisos y sandbox,
 * Diagnóstico y mantenimiento, y Avanzado.
 *
 * Es una página, no un diálogo: sustituye el contenido del área principal, con la barra lateral
 * y la cabecera de la app intactas. La navegación la coordina `App` (hash interno): aqui sólo se
 * avisa de la categoría elegida o de que se quiere volver.
 *
 * Los modelos no se editan aquí: `onEditarModelo` navega al editor, que es otra página completa
 * y el destino de «Agregar/editar» de la categoría Modelos. Todo lo que se muta pasa por
 * `onConfigCambiada`/`onAviso`/`onConfirmar`: la app es la única que recarga el estado, así que
 * esto no guarda copias paralelas de la config.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  CHAT_WATERMARK_OPACITY_MAX,
  CHAT_WATERMARK_OPACITY_MIN,
  CHAT_WATERMARK_OPACITY_STEP,
  IDIOMAS,
  TEXT_INTENSITIES,
  THEMES,
  isIdioma,
  isTextIntensity,
  type IdiomaId,
  type TextIntensityId,
  type ThemeId,
} from "../electron/themes";
import {
  ConfigView,
  DesktopControlView,
  ModelsResult,
  RolesView,
  SafeProxyConfigView,
  SafeProxyMode,
  SafeProxyState,
  SafeProxyView,
  SavedProviderView,
  ToolView,
  api,
  bridge,
  mensajeDe,
} from "./api";
import { useT, type Clave } from "./i18n";

/** Categorías de Ajustes; cada una se muestra como una página independiente. */
export type AjustesSeccion =
  | "modelos"
  | "roles"
  | "permisos"
  | "escritorio"
  | "safe-proxy"
  | "mantenimiento"
  | "avanzado"
  | "apariencia";

interface Categoria {
  id: AjustesSeccion;
  clave: Clave;
  resumenClave: Clave;
}

const CATEGORIAS: Categoria[] = [
  { id: "modelos", clave: "cat.modelos", resumenClave: "cat.modelos.resumen" },
  { id: "roles", clave: "cat.roles", resumenClave: "cat.roles.resumen" },
  { id: "permisos", clave: "cat.permisos", resumenClave: "cat.permisos.resumen" },
  { id: "escritorio", clave: "cat.escritorio", resumenClave: "cat.escritorio.resumen" },
  { id: "safe-proxy", clave: "cat.safeProxy", resumenClave: "cat.safeProxy.resumen" },
  {
    id: "mantenimiento",
    clave: "cat.mantenimiento",
    resumenClave: "cat.mantenimiento.resumen",
  },
  { id: "avanzado", clave: "cat.avanzado", resumenClave: "cat.avanzado.resumen" },
  { id: "apariencia", clave: "cat.apariencia", resumenClave: "cat.apariencia.resumen" },
];

const MODOS: { valor: string; clave: Clave }[] = [
  { valor: "ask", clave: "settings.permissions.mode.ask" },
  { valor: "auto", clave: "settings.permissions.mode.auto" },
  { valor: "unrestricted", clave: "settings.permissions.mode.unrestricted" },
];

/** Claves i18n del catálogo de temas: el id del tema no cambia, sólo lo que se lee. */
const CLAVES_TEMA: Record<ThemeId, { nombre: Clave; descripcion: Clave }> = {
  obsidiana: {
    nombre: "settings.theme.obsidiana.name",
    descripcion: "settings.theme.obsidiana.description",
  },
  grafito: { nombre: "settings.theme.grafito.name", descripcion: "settings.theme.grafito.description" },
  medianoche: {
    nombre: "settings.theme.medianoche.name",
    descripcion: "settings.theme.medianoche.description",
  },
  bosque: { nombre: "settings.theme.bosque.name", descripcion: "settings.theme.bosque.description" },
  claro: { nombre: "settings.theme.claro.name", descripcion: "settings.theme.claro.description" },
  deepdark: {
    nombre: "settings.theme.deepdark.name",
    descripcion: "settings.theme.deepdark.description",
  },
};

/** Claves i18n de los niveles de intensidad del blanco (Deep Dark). */
const CLAVES_INTENSIDAD: Record<TextIntensityId, { label: Clave; hint: Clave }> = {
  suave: { label: "settings.intensity.suave.label", hint: "settings.intensity.suave.hint" },
  medio: { label: "settings.intensity.medio.label", hint: "settings.intensity.medio.hint" },
  brillante: {
    label: "settings.intensity.brillante.label",
    hint: "settings.intensity.brillante.hint",
  },
  maximo: { label: "settings.intensity.maximo.label", hint: "settings.intensity.maximo.hint" },
};

/** Claves i18n del estado real del Safe Proxy. */
const CLAVES_ESTADO_PROXY: Record<SafeProxyState, Clave> = {
  internal: "settings.safeProxy.state.internal",
  stopped: "settings.safeProxy.state.stopped",
  running: "settings.safeProxy.state.running",
  "config-error": "settings.safeProxy.state.configError",
  "port-conflict": "settings.safeProxy.state.portConflict",
};

/** Claves i18n del selector de modo. */
const CLAVES_MODO_PROXY: Record<SafeProxyMode, Clave> = {
  internal: "settings.safeProxy.mode.internal",
  server: "settings.safeProxy.mode.server",
};

/** Campos editables de Safe Proxy (sólo la allowlist real del servicio). */
interface SafeProxyForm {
  allowed_models: string;
  block_terms: string;
  mask_terms: string;
  pii: string[];
  secrets_action: string;
  /** Detección/protección de claves de API (``policy.api_keys``). */
  api_keys: boolean;
  obfuscate_python: boolean;
  strip_comments: boolean;
  fail_closed: boolean;
  other_languages: string;
  timeout_seconds: number;
}

/** Opciones de PII que el proxy admite (valores técnicos, sin traducir). */
const PII_OPCIONES = ["EMAIL", "PHONE", "CURP", "RFC", "CLABE", "CARD", "IP"] as const;

/** Una línea = un término (se ignoran líneas vacías). */
function lineas(valor: string): string[] {
  return valor
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Construye el formulario a partir de la config redactada que devuelve el proxy. */
function formularioProxy(config: SafeProxyConfigView | null): SafeProxyForm {
  const policy = (config?.policy ?? {}) as Record<string, unknown>;
  const code = (config?.code ?? {}) as Record<string, unknown>;
  const upstream = (config?.upstream ?? {}) as Record<string, unknown>;
  const texto = (valor: unknown) =>
    Array.isArray(valor) ? valor.map((item) => String(item)).join("\n") : "";
  const numero = typeof upstream.timeout_seconds === "number" ? upstream.timeout_seconds : 180;
  return {
    allowed_models: texto(policy.allowed_models),
    block_terms: texto(policy.block_terms),
    mask_terms: texto(policy.mask_terms),
    pii: Array.isArray(policy.pii) ? policy.pii.map((item) => String(item).toUpperCase()) : [],
    secrets_action: String(policy.secrets_action ?? "block"),
    api_keys: policy.api_keys !== false,
    obfuscate_python: code.obfuscate_python !== false,
    strip_comments: code.strip_comments !== false,
    fail_closed: code.fail_closed !== false,
    other_languages: String(code.other_languages ?? "block"),
    timeout_seconds: numero,
  };
}

interface Props {
  config: ConfigView;
  safeMode: boolean;
  /** Categoría abierta; `null` = página principal de Ajustes. */
  seccion: AjustesSeccion | null;
  /** Tema de apariencia activo. */
  tema: ThemeId;
  /** Aplica y persiste un tema nuevo (se ve al instante). */
  onTema: (tema: ThemeId) => void;
  /** Intensidad del blanco (sólo Deep Dark la usa). */
  intensidad: TextIntensityId;
  /** Aplica y persiste la intensidad del blanco. */
  onIntensidad: (intensidad: TextIntensityId) => void;
  /** Marca de agua del chat: visible/oculta (persistida junto al tema). */
  marca: boolean;
  onMarca: (activa: boolean) => void;
  /** Opacidad elegida de la marca (se conserva aunque esté oculta). */
  marcaOpacidad: number;
  onMarcaOpacidad: (valor: number) => void;
  /** Idioma de la interfaz (español / inglés). */
  idioma: IdiomaId;
  onIdioma: (idioma: IdiomaId) => void;
  /** Navega a una categoría (o vuelve a la principal con `null`). */
  onSeccion: (seccion: AjustesSeccion | null) => void;
  /** Vuelve a la conversación (sólo desde la página principal). */
  onCerrar: () => void;
  onPolitica: (modo: string) => void;
  onModo: (activo: boolean) => void;
  /** Abre el editor de modelo a pantalla completa (`null` = agregar uno nuevo). */
  onEditarModelo: (proveedor: SavedProviderView | null) => void;
  onConfigCambiada: () => void;
  onAviso: (mensaje: string) => void;
  onConfirmar: (titulo: string, detalle: string, accion: () => void) => void;
}

export function Settings(props: Props) {
  const t = useT();
  // --- roles ---
  const [roles, setRoles] = useState<RolesView | null>(null);

  // --- control de escritorio (F7) ---
  const [desktop, setDesktop] = useState<DesktopControlView | null>(null);
  const [visionModelos, setVisionModelos] = useState<ModelsResult | null>(null);
  const [cargandoVision, setCargandoVision] = useState(false);

  // --- Safe Proxy (servicio embebido: estado real, arranque/parada y opciones) ---
  const [safeProxy, setSafeProxy] = useState<SafeProxyView | null>(null);
  const [proxyPort, setProxyPort] = useState("");
  const [proxyForm, setProxyForm] = useState<SafeProxyForm>(() => formularioProxy(null));

  // ChatGPT plan account (tokens remain inside Electron's main process).
  const [chatgptAccount, setChatgptAccount] = useState({ connected: false, email: "" });
  const [chatgptModels, setChatgptModels] = useState<{ id: string; display_name: string }[]>([]);
  const [chatgptModel, setChatgptModel] = useState("");
  const [chatgptBusy, setChatgptBusy] = useState(false);
  const [claudeModels, setClaudeModels] = useState<{ id: string; display_name: string }[]>([]);
  const [claudeModel, setClaudeModel] = useState("sonnet");
  const [claudeAvailable, setClaudeAvailable] = useState(false);
  const [claudeBusy, setClaudeBusy] = useState(false);

  // --- mantenimiento ---
  const [salida, setSalida] = useState("");
  const [salidaSandbox, setSalidaSandbox] = useState("");
  const [herramientas, setHerramientas] = useState<ToolView[] | null>(null);
  const [instantaneas, setInstantaneas] = useState<string[] | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setRoles(await api<RolesView>("/roles"));
      } catch (exc) {
        props.onAviso(mensajeDe(exc));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let vigente = true;
    void api<{ available: boolean; models: { id: string; display_name: string }[] }>(
      "/providers/claude-models",
    ).then((result) => {
      if (!vigente) return;
      setClaudeAvailable(result.available);
      setClaudeModels(result.models ?? []);
      setClaudeModel(result.models?.[0]?.id ?? "sonnet");
    }).catch((exc) => {
      if (vigente) props.onAviso(mensajeDe(exc));
    });
    return () => { vigente = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function iniciarSesionClaude(): Promise<void> {
    setClaudeBusy(true);
    try {
      const result = await bridge.claude.login();
      if (!result.launched) throw new Error(result.message || t("settings.models.claude.terminalMissing"));
      props.onAviso(t("settings.models.claude.loginOpened"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    } finally {
      setClaudeBusy(false);
    }
  }

  async function guardarClaude(): Promise<void> {
    if (!claudeModel) return;
    setClaudeBusy(true);
    try {
      await api<ConfigView>("/providers/claude-account", {
        method: "POST",
        body: { model: claudeModel },
      });
      props.onConfigCambiada();
      props.onAviso(t("settings.models.claude.saved", { model: claudeModel }));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    } finally {
      setClaudeBusy(false);
    }
  }

  useEffect(() => {
    let vigente = true;
    void bridge.chatgpt.status().then(async (status) => {
      if (!vigente) return;
      setChatgptAccount(status);
      if (!status.connected) return;
      try {
        const result = await api<{ ok: boolean; models: { id: string; display_name: string }[] }>(
          "/providers/chatgpt-models",
        );
        if (vigente) {
          setChatgptModels(result.models ?? []);
          setChatgptModel(result.models?.[0]?.id ?? "");
        }
      } catch (exc) {
        if (vigente) props.onAviso(mensajeDe(exc));
      }
    }).catch((exc) => {
      if (vigente) props.onAviso(mensajeDe(exc));
    });
    return () => { vigente = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function conectarChatGPT(): Promise<void> {
    setChatgptBusy(true);
    try {
      const account = await bridge.chatgpt.login();
      setChatgptAccount({ connected: true, email: account.email });
      const result = await api<{ ok: boolean; models: { id: string; display_name: string }[] }>(
        "/providers/chatgpt-models",
      );
      setChatgptModels(result.models ?? []);
      setChatgptModel(result.models?.[0]?.id ?? "");
      props.onAviso(t("settings.models.chatgpt.connected"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    } finally {
      setChatgptBusy(false);
    }
  }

  async function desconectarChatGPT(): Promise<void> {
    setChatgptBusy(true);
    try {
      setChatgptAccount(await bridge.chatgpt.disconnect());
      setChatgptModels([]);
      setChatgptModel("");
      props.onAviso(t("settings.models.chatgpt.disconnected"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    } finally {
      setChatgptBusy(false);
    }
  }

  async function guardarChatGPT(): Promise<void> {
    if (!chatgptModel) return;
    setChatgptBusy(true);
    try {
      await api<ConfigView>("/providers/chatgpt-account", {
        method: "POST",
        body: { model: chatgptModel },
      });
      props.onConfigCambiada();
      props.onAviso(t("settings.models.chatgpt.saved", { model: chatgptModel }));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    } finally {
      setChatgptBusy(false);
    }
  }

  // Los roles se apoyan en la lista de proveedores guardados. Cuando /config cambia (tras
  // guardar/activar/quitar un proveedor) se vuelve a pedir /roles: así el desplegable no se
  // queda con un catálogo obsoleto ni con selecciones que ya no existen.
  const primerRoles = useRef(true);
  useEffect(() => {
    if (primerRoles.current) {
      primerRoles.current = false;
      return;
    }
    void (async () => {
      try {
        setRoles(await api<RolesView>("/roles"));
      } catch (exc) {
        props.onAviso(mensajeDe(exc));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.config.providers, props.config.active_provider]);

  // --- control de escritorio (F7): estado local + selección de visión ---
  useEffect(() => {
    let vigente = true;
    void api<DesktopControlView>("/desktop-control")
      .then((vista) => {
        if (vigente) setDesktop(vista);
      })
      .catch((exc) => props.onAviso(mensajeDe(exc)));
    return () => {
      vigente = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.config.providers]);

  useEffect(() => {
    const proveedor = desktop?.vision.provider ?? "";
    if (props.seccion !== "escritorio" || !proveedor) {
      setVisionModelos(null);
      return;
    }
    let vigente = true;
    setCargandoVision(true);
    void api<ModelsResult>(`/models?provider=${encodeURIComponent(proveedor)}`)
      .then((datos) => {
        if (vigente) setVisionModelos(datos);
      })
      .catch(() => {
        if (vigente) setVisionModelos(null);
      })
      .finally(() => {
        if (vigente) setCargandoVision(false);
      });
    return () => {
      vigente = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop?.vision.provider, props.seccion]);

  // Safe Proxy: se lee al montar (es estado del host, no depende del proyecto abierto).
  useEffect(() => {
    let vigente = true;
    void api<SafeProxyView>("/safe-proxy")
      .then((vista) => {
        if (!vigente) return;
        setSafeProxy(vista);
        setProxyPort(String(vista.port));
      })
      .catch((exc) => props.onAviso(mensajeDe(exc)));
    return () => {
      vigente = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cuando el proxy responde, se vuelca su config redactada en el formulario.
  useEffect(() => {
    if (safeProxy?.config) setProxyForm(formularioProxy(safeProxy.config));
  }, [safeProxy]);

  /** El doble paso por `unknown` deja el campo utilizable tanto si el backend ya envía la
   * vista tipada como si viene como registro genérico: aquí sólo se leen claves conocidas. */
  const guardados = useMemo(
    () =>
      Array.isArray(props.config.providers)
        ? (props.config.providers as unknown as SavedProviderView[])
        : [],
    [props.config.providers],
  );

  /** Guarda el flag local y/o la selección de visión; el backend valida el proveedor. */
  async function cambiarDesktop(patch: Record<string, unknown>, mensaje = "") {
    try {
      const vista = await api<DesktopControlView>("/desktop-control", {
        method: "PATCH",
        body: patch,
      });
      setDesktop(vista);
      if (vista.runtime_error) {
        props.onAviso(
          t("settings.desktop.savedNoRuntime", { error: vista.runtime_error }),
        );
      } else if (mensaje) {
        props.onAviso(mensaje);
      }
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Aplica una vista nueva del servicio (y sincroniza el puerto mostrado). */
  function aplicarVistaProxy(vista: SafeProxyView) {
    setSafeProxy(vista);
    setProxyPort(String(vista.port));
  }

  /** Vuelve a leer el estado real del servicio embebido. */
  async function refrescarProxy() {
    try {
      aplicarVistaProxy(await api<SafeProxyView>("/safe-proxy"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Enciende o apaga el servicio embebido (arranque/parada reales, in-process). */
  async function alternarServicioProxy(encender: boolean) {
    try {
      const vista = await api<SafeProxyView>(encender ? "/safe-proxy/start" : "/safe-proxy/stop", {
        method: "POST",
      });
      aplicarVistaProxy(vista);
      if (vista.state === "stopped" || vista.state === "running") {
        props.onAviso(
          encender ? t("settings.safeProxy.started") : t("settings.safeProxy.stopped"),
        );
      }
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Cambia de modo: interno (sin puerto) o servidor (loopback). */
  async function cambiarModoProxy(modo: SafeProxyMode) {
    try {
      const vista = await api<SafeProxyView>("/safe-proxy/mode", {
        method: "POST",
        body: { mode: modo },
      });
      aplicarVistaProxy(vista);
      props.onAviso(t("settings.safeProxy.mode.saved"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Guarda el puerto donde escucha el servicio (host loopback fijo). */
  async function guardarPuertoProxy() {
    try {
      const vista = await api<SafeProxyView>("/safe-proxy/connection", {
        method: "POST",
        body: { port: Number(proxyPort) },
      });
      aplicarVistaProxy(vista);
      props.onAviso(t("settings.safeProxy.savedPort"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Habilita/deshabilita el routing real del servicio (llama a enable/disable). */
  async function alternarProxy(habilitado: boolean) {
    try {
      const vista = await api<SafeProxyView>("/safe-proxy/toggle", {
        method: "POST",
        body: { enabled: habilitado },
      });
      aplicarVistaProxy(vista);
      props.onAviso(
        habilitado ? t("settings.safeProxy.enabled") : t("settings.safeProxy.disabled"),
      );
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Aplica la allowlist en el proxy por PATCH y refresca la vista. */
  async function guardarProxyConfig() {
    try {
      const vista = await api<SafeProxyView>("/safe-proxy/config", {
        method: "PATCH",
        body: {
          policy: {
            allowed_models: lineas(proxyForm.allowed_models),
            block_terms: lineas(proxyForm.block_terms),
            mask_terms: lineas(proxyForm.mask_terms),
            pii: proxyForm.pii,
            secrets_action: proxyForm.secrets_action,
            api_keys: proxyForm.api_keys,
          },
          code: {
            obfuscate_python: proxyForm.obfuscate_python,
            strip_comments: proxyForm.strip_comments,
            fail_closed: proxyForm.fail_closed,
            other_languages: proxyForm.other_languages,
          },
          upstream: { timeout_seconds: Number(proxyForm.timeout_seconds) },
        },
      });
      setSafeProxy(vista);
      props.onAviso(t("settings.safeProxy.savedConfig"));
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  /** Persiste el esfuerzo del proveedor (sólo si su modelo lo admite; lo valida el backend). */
  async function cambiarEffort(prov: SavedProviderView, valor: string) {
    try {
      await api<ConfigView>(`/providers/${encodeURIComponent(prov.id)}`, {
        method: "PATCH",
        body: { reasoning_effort: valor },
      });
      props.onConfigCambiada();
      props.onAviso(
        t("settings.models.effort.changed", { proveedor: prov.name, esfuerzo: valor || "auto" }),
      );
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  async function aplicarRol(
    rol: string,
    proveedor: string,
    model: string,
    esfuerzo = "",
    think: boolean | null = null,
    niveles: string[] = [],
    control = "",
  ) {
    try {
      const vista = await api<RolesView>("/roles", {
        method: "PUT",
        body: {
          role: rol,
          provider: proveedor,
          model,
          reasoning_effort: esfuerzo,
          think,
          effort_levels: niveles,
          effort_control: control,
        },
      });
      setRoles(vista);
      props.onConfigCambiada();
      if (vista.runtime_error) {
        props.onAviso(t("settings.roles.savedNoRuntime", { rol, error: vista.runtime_error }));
      } else {
        props.onAviso(
          t("settings.roles.applied", {
            rol,
            detalle:
              esfuerzo && esfuerzo !== "auto"
                ? t("settings.roles.applied.effort", { esfuerzo })
                : "",
          }),
        );
      }
    } catch (exc) {
      props.onAviso(mensajeDe(exc));
    }
  }

  function quitarProveedor(prov: SavedProviderView) {
    props.onConfirmar(
      t("settings.models.remove.title", { proveedor: prov.name }),
      t("settings.models.remove.detail"),
      () => {
        void (async () => {
          try {
            await api(`/providers/${encodeURIComponent(prov.id)}`, { method: "DELETE" });
            props.onConfigCambiada();
            props.onAviso(t("settings.models.removed", { proveedor: prov.name }));
          } catch (exc) {
            props.onAviso(mensajeDe(exc));
          }
        })();
      },
    );
  }

  /** Mantenimiento en general: se vuelca la respuesta tal cual, sin resumir por cuenta propia. */
  async function ejecutar(
    ruta: string,
    titulo: string,
    opciones: { metodo?: string; cuerpo?: unknown; destino?: "salida" | "sandbox" } = {},
  ) {
    const { metodo = "GET", cuerpo, destino = "salida" } = opciones;
    const fijar = destino === "sandbox" ? setSalidaSandbox : setSalida;
    try {
      const datos = await api<unknown>(ruta, {
        method: metodo,
        ...(cuerpo === undefined ? {} : { body: cuerpo }),
      });
      fijar(`${titulo}\n\n${JSON.stringify(datos, null, 2)}`);
      props.onConfigCambiada();
    } catch (exc) {
      fijar(`${titulo}\n\n${mensajeDe(exc)}`);
    }
  }

  async function cargarHerramientas() {
    try {
      const datos = await api<{ tools: ToolView[] }>("/tools");
      setHerramientas(Array.isArray(datos?.tools) ? datos.tools : []);
      setSalida("");
    } catch (exc) {
      setHerramientas(null);
      setSalida(`${t("settings.maintenance.tools")}\n\n${mensajeDe(exc)}`);
    }
  }

  async function cargarSnapshots() {
    try {
      const datos = await api<{ snapshots: string[] }>("/snapshots");
      setInstantaneas(
        Array.isArray(datos?.snapshots) ? datos.snapshots.map((item) => String(item)) : [],
      );
      setSalida("");
    } catch (exc) {
      setInstantaneas(null);
      setSalida(`${t("settings.maintenance.snapshots")}\n\n${mensajeDe(exc)}`);
    }
  }

  function restaurarSnapshot(nombreSnapshot: string) {
    props.onConfirmar(
      t("settings.maintenance.restore.confirm.title", { nombre: nombreSnapshot }),
      t("settings.maintenance.restore.confirm.detail"),
      () => {
        void ejecutar(
          "/restore",
          t("settings.maintenance.restore.run", { nombre: nombreSnapshot }),
          {
            metodo: "POST",
            cuerpo: { name: nombreSnapshot, confirm: true },
          },
        );
      },
    );
  }

  const titulo =
    props.seccion === null
      ? ""
      : (() => {
          const categoria = CATEGORIAS.find((item) => item.id === props.seccion);
          return categoria ? t(categoria.clave) : "";
        })();

  /** Línea de contexto de cada categoría en la página principal. */
  function metaDe(id: AjustesSeccion): string {
    if (id === "modelos") {
      const activo = guardados.find((prov) => prov.id === props.config.active_provider);
      const nombre = activo
        ? `${activo.name}${activo.model ? ` · ${activo.model}` : ""}`
        : t("settings.models.meta.noActive");
      return t("settings.models.meta.count", { n: guardados.length, nombre });
    }
    if (id === "roles") {
      return t("settings.roles.meta", { n: roles?.roles.length ?? 0 });
    }
    if (id === "permisos") {
      const seguro = props.safeMode ? t("settings.permissions.meta.safe") : "";
      return t("settings.permissions.meta", {
        modo: props.config.policy_mode,
        nivel: props.config.sandbox,
        seguro,
      });
    }
    if (id === "escritorio") {
      if (!desktop) return t("settings.desktop.meta.optional");
      const estado = desktop.enabled
        ? t("settings.desktop.meta.on")
        : t("settings.desktop.meta.off");
      return desktop.vision.configured
        ? `${estado} · ${desktop.vision.provider}`
        : t("settings.desktop.meta.none", { estado });
    }
    if (id === "mantenimiento") {
      return t("settings.maintenance.meta");
    }
    if (id === "avanzado") {
      return t("settings.advanced.meta");
    }
    if (id === "safe-proxy") {
      if (!safeProxy) return t("settings.safeProxy.meta.internal");
      if (safeProxy.state === "internal") return t("settings.safeProxy.meta.internal");
      if (safeProxy.state === "config-error" || safeProxy.state === "port-conflict") {
        return t("settings.safeProxy.meta.error");
      }
      return safeProxy.state === "running"
        ? t("settings.safeProxy.meta.running")
        : t("settings.safeProxy.meta.stopped");
    }
    const tema = THEMES.find((item) => item.id === props.tema) ?? THEMES[0];
    const nombreTema = t(CLAVES_TEMA[tema.id].nombre);
    if (tema.id === "deepdark") {
      const nivel = TEXT_INTENSITIES.find((item) => item.id === props.intensidad) ?? TEXT_INTENSITIES[1];
      return t("settings.appearance.meta.deepdark", {
        tema: nombreTema,
        nivel: t(CLAVES_INTENSIDAD[nivel.id].label).toLowerCase(),
      });
    }
    return `${t("settings.appearance.meta.theme", { tema: nombreTema })}${
      tema.light ? t("settings.appearance.tone.light") : t("settings.appearance.tone.dark")
    }`;
  }

  // ---------------------------------------------------------------- páginas

  function paginaModelos() {
    return (
      <section className="tarjeta">
        <p className="nota">{t("settings.models.note")}</p>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.models.chatgpt.title")}</p>
          <p className="nota">{t("settings.models.chatgpt.note")}</p>
          {chatgptAccount.connected ? (
            <>
              <p className="meta">{t("settings.models.chatgpt.connectedAs", { email: chatgptAccount.email || "ChatGPT" })}</p>
              <div className="acciones">
                <select value={chatgptModel} onChange={(event) => setChatgptModel(event.target.value)}>
                  {chatgptModels.map((model) => (
                    <option key={model.id} value={model.id}>{model.display_name}</option>
                  ))}
                </select>
                <button className="primary" disabled={chatgptBusy || !chatgptModel} onClick={() => void guardarChatGPT()}>
                  {t("settings.models.chatgpt.useModel")}
                </button>
                <button className="quiet" disabled={chatgptBusy} onClick={() => void desconectarChatGPT()}>
                  {t("settings.models.chatgpt.disconnect")}
                </button>
              </div>
            </>
          ) : (
            <button className="primary" disabled={chatgptBusy} onClick={() => void conectarChatGPT()}>
              {t("settings.models.chatgpt.signIn")}
            </button>
          )}
        </div>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.models.claude.title")}</p>
          <p className="nota">{t("settings.models.claude.note")}</p>
          <div className="acciones">
            <button className="primary" disabled={claudeBusy || !claudeAvailable} onClick={() => void iniciarSesionClaude()}>
              {t("settings.models.claude.signIn")}
            </button>
            <select value={claudeModel} onChange={(event) => setClaudeModel(event.target.value)}>
              {claudeModels.map((model) => (
                <option key={model.id} value={model.id}>{model.display_name}</option>
              ))}
            </select>
            <button className="primary" disabled={claudeBusy || !claudeModel || !claudeAvailable} onClick={() => void guardarClaude()}>
              {t("settings.models.claude.useModel")}
            </button>
          </div>
          {!claudeAvailable && <p className="meta">{t("settings.models.claude.installRequired")}</p>}
        </div>

        <div className="acciones" style={{ marginTop: 0 }}>
          <button className="primary" onClick={() => props.onEditarModelo(null)}>
            {t("settings.models.add")}
          </button>
        </div>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.models.saved")}</p>
          {guardados.length === 0 && (
            <div className="vacio">{t("settings.models.empty")}</div>
          )}
          {guardados.length > 0 && (
            <div className="lista-prov">
              {guardados.map((prov) => {
                const activo = prov.id === props.config.active_provider;
                const niveles = prov.effort_options ?? [];
                const guardado = prov.reasoning_effort ?? "";
                const esfuerzo = guardado || "auto";
                return (
                  <div key={prov.id} className="item" style={{ cursor: "default" }}>
                    <span className="nombre">{prov.name}</span>
                    <span className="meta">{prov.model || prov.base_url}</span>
                    <button className="quiet" onClick={() => props.onEditarModelo(prov)}>
                      {t("settings.models.edit")}
                    </button>
                    {niveles.length > 0 ? (
                      <select
                        style={{ width: "auto" }}
                        value={esfuerzo}
                        title={t("settings.models.effort.title")}
                        aria-label={t("settings.models.effort.aria", { proveedor: prov.name })}
                        onChange={(evento) => void cambiarEffort(prov, evento.target.value)}
                      >
                        {niveles.map((nivel) => (
                          <option key={nivel} value={nivel}>
                            {nivel}
                          </option>
                        ))}
                        {guardado && !niveles.includes(guardado) && (
                          <option value={guardado}>{guardado}</option>
                        )}
                      </select>
                    ) : (
                      <span className="meta" title={t("settings.models.effort.none.title")}>
                        {t("settings.models.effort.na")}
                      </span>
                    )}
                    {activo ? (
                      <span className="chip acento">{t("settings.models.active")}</span>
                    ) : (
                      <button className="peligro" onClick={() => quitarProveedor(prov)}>
                        {t("settings.models.remove")}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <p className="nota">
          {t("settings.models.footer", {
            activo: props.config.active_provider || t("settings.models.none"),
          })}
        </p>
      </section>
    );
  }

  function paginaRoles() {
    return (
      <section className="tarjeta">
        <p className="nota">{t("settings.roles.note")}</p>
        {roles?.roles.map((rol) => (
          <FilaRol
            key={rol}
            rol={rol}
            actual={roles.configured[rol] ?? {}}
            proveedores={guardados}
            global={props.config.active_provider}
            onAplicar={(proveedor, model, esfuerzo, think, niveles, control) =>
              void aplicarRol(rol, proveedor, model, esfuerzo, think, niveles, control)
            }
          />
        ))}
        {guardados.length === 0 && (
          <div className="vacio">{t("settings.roles.empty")}</div>
        )}
      </section>
    );
  }

  function paginaPermisos() {
    return (
      <section className="tarjeta">
        {MODOS.map((modo) => (
          <label key={modo.valor} className="opcion">
            <input
              type="radio"
              name="politica"
              checked={props.config.policy_mode === modo.valor}
              onChange={() => props.onPolitica(modo.valor)}
            />
            {t(modo.clave)}
          </label>
        ))}
        <label className="opcion">
          <input
            type="checkbox"
            checked={props.safeMode}
            onChange={(e) => props.onModo(e.target.checked)}
          />
          {t("settings.permissions.safeMode")}
        </label>
        <p className="nota">
          {t("settings.permissions.sandbox.note.a", { nivel: props.config.sandbox })}
          <strong>{t("settings.permissions.sandbox.note.no")}</strong>
          {t("settings.permissions.sandbox.note.b")}
        </p>
        <div className="acciones">
          <button
            onClick={() =>
              void ejecutar("/setup", "setup (sandbox)", { metodo: "POST", destino: "sandbox" })
            }
          >
            {t("settings.permissions.setup")}
          </button>
        </div>
        {salidaSandbox && <pre className="salida">{salidaSandbox}</pre>}
      </section>
    );
  }

  function paginaEscritorio() {
    const vision = desktop?.vision;
    const modelos = visionModelos?.ok ? visionModelos.models : [];
    const visionModel = vision?.model ?? "";
    return (
      <section className="tarjeta">
        <p className="nota" style={{ marginTop: 0 }}>
          {t("settings.desktop.note.a")}
          <strong>{t("settings.desktop.note.no")}</strong>
          {t("settings.desktop.note.b")}
        </p>

        <label className="opcion">
          <input
            type="checkbox"
            checked={Boolean(desktop?.enabled)}
            disabled={!desktop}
            onChange={(evento) =>
              void cambiarDesktop(
                { enabled: evento.target.checked },
                evento.target.checked
                  ? t("settings.desktop.enabled")
                  : t("settings.desktop.disabled"),
              )
            }
          />
          {t("settings.desktop.toggle")}
        </label>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.desktop.vision.title")}</p>
          {guardados.length === 0 ? (
            <div className="vacio">
              {t("settings.desktop.vision.empty")}{" "}
              <button className="quiet" onClick={() => props.onSeccion("modelos")}>
                {t("settings.desktop.vision.goModels")}
              </button>
            </div>
          ) : (
            <div className="rejilla" style={{ alignItems: "end" }}>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("settings.desktop.vision.provider")}</label>
                <select
                  value={vision?.provider ?? ""}
                  aria-label={t("settings.desktop.vision.provider.aria")}
                  disabled={!desktop}
                  onChange={(evento) =>
                    void cambiarDesktop(
                      { vision_provider: evento.target.value },
                      t("settings.desktop.vision.provider.saved"),
                    )
                  }
                >
                  <option value="">{t("settings.desktop.vision.noProvider")}</option>
                  {guardados.map((prov) => (
                    <option key={prov.id} value={prov.id}>
                      {prov.name} · {prov.model || prov.id}
                    </option>
                  ))}
                </select>
              </div>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("settings.desktop.vision.model")}</label>
                <select
                  value={visionModel}
                  aria-label={t("settings.desktop.vision.model.aria")}
                  disabled={!desktop || !vision?.provider}
                  onChange={(evento) =>
                    void cambiarDesktop(
                      { vision_model: evento.target.value },
                      t("settings.desktop.vision.model.saved"),
                    )
                  }
                >
                  <option value="">{t("settings.desktop.vision.providerDefault")}</option>
                  {modelos.map((modelo) => (
                    <option key={modelo} value={modelo}>
                      {modelo}
                    </option>
                  ))}
                  {visionModel && !modelos.includes(visionModel) && (
                    <option value={visionModel}>{visionModel}</option>
                  )}
                </select>
              </div>
            </div>
          )}
          {cargandoVision && <p className="nota">{t("settings.models.loading")}</p>}
        </div>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.desktop.availability")}</p>
          <p className="nota" style={{ marginTop: 0 }}>
            {vision?.configured
              ? t("settings.desktop.selected", {
                  proveedor: vision.provider,
                  modelo: vision.model,
                })
              : vision?.reason || t("settings.desktop.noVision")}{" "}
            <strong>{t("settings.desktop.notImplemented")}</strong>
            {t("settings.desktop.notImplemented.note")}
          </p>
          <p className="nota">{t("settings.desktop.ocr")}</p>
          {desktop && (
            <p className="nota">
              {t("settings.desktop.guarantees", {
                mcp: desktop.guards.mcp
                  ? t("settings.desktop.mcp.on")
                  : t("settings.desktop.mcp.off"),
              })}
            </p>
          )}
        </div>
      </section>
    );
  }

  function paginaSafeProxy() {
    const vista = safeProxy;
    const modo: SafeProxyMode = vista?.mode ?? "internal";
    const interno = modo === "internal";
    const enMarcha = vista?.state === "running";
    const activo = interno ? Boolean(vista?.internal_enabled) : enMarcha;
    const estado = vista
      ? t(CLAVES_ESTADO_PROXY[vista.state])
      : t("settings.safeProxy.state.internal");
    const piiActiva = (valor: string) => proxyForm.pii.includes(valor);
    const alternarPii = (valor: string, activo2: boolean) =>
      setProxyForm((previo) => ({
        ...previo,
        pii: activo2 ? [...previo.pii, valor] : previo.pii.filter((item) => item !== valor),
      }));
    return (
      <section className="tarjeta">
        <p className="nota" style={{ marginTop: 0 }}>
          {t("settings.safeProxy.note")}
        </p>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.safeProxy.mode")}</p>
          <div className="campo" style={{ marginBottom: 0 }}>
            <select
              value={modo}
              aria-label={t("settings.safeProxy.mode.aria")}
              onChange={(evento) => void cambiarModoProxy(evento.target.value as SafeProxyMode)}
            >
              {(["internal", "server"] as SafeProxyMode[]).map((cada) => (
                <option key={cada} value={cada}>
                  {t(CLAVES_MODO_PROXY[cada])}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="subbloque">
          <p className="etiqueta">{t("settings.safeProxy.service")}</p>
          <p className="nota" style={{ marginTop: 0 }}>
            <span className={`chip${activo ? " acento" : ""}`}>{estado}</span>
            {vista?.error ? ` ${vista.error}` : ""}
          </p>

          {interno ? (
            <>
              <label className="opcion">
                <input
                  type="checkbox"
                  checked={Boolean(vista?.internal_enabled)}
                  onChange={(evento) => void alternarProxy(evento.target.checked)}
                />
                {t("settings.safeProxy.internal.toggle")}
              </label>
              <p className="nota" style={{ margin: 0 }}>
                {t("settings.safeProxy.internal.note")}
              </p>
            </>
          ) : (
            <div className="acciones" style={{ marginTop: 0 }}>
              <button
                className="primary"
                disabled={enMarcha}
                onClick={() => void alternarServicioProxy(true)}
              >
                {t("settings.safeProxy.start")}
              </button>
              <button disabled={!enMarcha} onClick={() => void alternarServicioProxy(false)}>
                {t("settings.safeProxy.stop")}
              </button>
              <button onClick={() => void refrescarProxy()}>
                {t("settings.safeProxy.refresh")}
              </button>
            </div>
          )}
        </div>

        {!interno && (
          <div className="subbloque">
            <p className="etiqueta">{t("settings.safeProxy.bind")}</p>
            <div className="rejilla" style={{ alignItems: "end" }}>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("settings.safeProxy.port")}</label>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={proxyPort}
                  aria-label={t("settings.safeProxy.port.aria")}
                  onChange={(evento) => setProxyPort(evento.target.value)}
                />
              </div>
              <div className="acciones" style={{ marginTop: 0 }}>
                <button onClick={() => void guardarPuertoProxy()}>
                  {t("settings.safeProxy.save")}
                </button>
              </div>
            </div>
            <p className="nota" style={{ margin: 0 }}>
              {t("settings.safeProxy.host.note")}
            </p>
            <p className="nota" style={{ margin: 0 }}>
              {t("settings.safeProxy.token.note")}
            </p>
          </div>
        )}

        {!interno && enMarcha && (
          <div className="subbloque">
            <p className="etiqueta">{t("settings.safeProxy.routing")}</p>
            <label className="opcion">
              <input
                type="checkbox"
                checked={Boolean(vista?.enabled)}
                disabled={vista?.enabled === null}
                onChange={(evento) => void alternarProxy(evento.target.checked)}
              />
              {t("settings.safeProxy.toggle")}
            </label>
          </div>
        )}

        {(interno || enMarcha) && vista?.config && (
          <div className="subbloque">
            <p className="etiqueta">{t("settings.safeProxy.config.title")}</p>
            <p className="nota" style={{ marginTop: 0 }}>
              {t("settings.safeProxy.config.note")}
            </p>
            <div className="rejilla">
              <label className="campo">
                <span>{t("settings.safeProxy.config.allowedModels")}</span>
                <textarea
                  rows={3}
                  value={proxyForm.allowed_models}
                  aria-label={t("settings.safeProxy.config.allowedModels")}
                  onChange={(evento) =>
                    setProxyForm((previo) => ({ ...previo, allowed_models: evento.target.value }))
                  }
                />
                <span className="nota" style={{ margin: 0 }}>
                  {t("settings.safeProxy.config.lines.hint")}
                </span>
              </label>
              <label className="campo">
                <span>{t("settings.safeProxy.config.blockTerms")}</span>
                <textarea
                  rows={3}
                  value={proxyForm.block_terms}
                  aria-label={t("settings.safeProxy.config.blockTerms")}
                  onChange={(evento) =>
                    setProxyForm((previo) => ({ ...previo, block_terms: evento.target.value }))
                  }
                />
                <span className="nota" style={{ margin: 0 }}>
                  {t("settings.safeProxy.config.lines.hint")}
                </span>
              </label>
              <label className="campo">
                <span>{t("settings.safeProxy.config.maskTerms")}</span>
                <textarea
                  rows={3}
                  value={proxyForm.mask_terms}
                  aria-label={t("settings.safeProxy.config.maskTerms")}
                  onChange={(evento) =>
                    setProxyForm((previo) => ({ ...previo, mask_terms: evento.target.value }))
                  }
                />
                <span className="nota" style={{ margin: 0 }}>
                  {t("settings.safeProxy.config.lines.hint")}
                </span>
              </label>
            </div>

            <div className="subbloque">
              <p className="etiqueta">{t("settings.safeProxy.config.pii")}</p>
              {PII_OPCIONES.map((opcion) => (
                <label key={opcion} className="opcion">
                  <input
                    type="checkbox"
                    checked={piiActiva(opcion)}
                    onChange={(evento) => alternarPii(opcion, evento.target.checked)}
                  />
                  {opcion}
                </label>
              ))}
            </div>

            <div className="rejilla" style={{ alignItems: "end" }}>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("settings.safeProxy.config.secretsAction")}</label>
                <select
                  value={proxyForm.secrets_action}
                  aria-label={t("settings.safeProxy.config.secretsAction")}
                  onChange={(evento) =>
                    setProxyForm((previo) => ({ ...previo, secrets_action: evento.target.value }))
                  }
                >
                  <option value="block">
                    {t("settings.safeProxy.config.secretsAction.block")}
                  </option>
                  <option value="redact">
                    {t("settings.safeProxy.config.secretsAction.redact")}
                  </option>
                </select>
              </div>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("settings.safeProxy.config.otherLanguages")}</label>
                <select
                  value={proxyForm.other_languages}
                  aria-label={t("settings.safeProxy.config.otherLanguages")}
                  onChange={(evento) =>
                    setProxyForm((previo) => ({ ...previo, other_languages: evento.target.value }))
                  }
                >
                  <option value="block">
                    {t("settings.safeProxy.config.otherLanguages.block")}
                  </option>
                  <option value="allow">
                    {t("settings.safeProxy.config.otherLanguages.allow")}
                  </option>
                </select>
              </div>
              <div className="campo" style={{ marginBottom: 0 }}>
                <label>{t("settings.safeProxy.config.timeout")}</label>
                <input
                  type="number"
                  min={1}
                  max={3600}
                  value={proxyForm.timeout_seconds}
                  aria-label={t("settings.safeProxy.config.timeout.aria")}
                  onChange={(evento) =>
                    setProxyForm((previo) => ({
                      ...previo,
                      timeout_seconds: Number(evento.target.value),
                    }))
                  }
                />
              </div>
            </div>

            <label className="opcion" title={t("settings.safeProxy.config.apiKeys.hint")}>
              <input
                type="checkbox"
                checked={proxyForm.api_keys}
                aria-label={t("settings.safeProxy.config.apiKeys.aria")}
                onChange={(evento) =>
                  setProxyForm((previo) => ({ ...previo, api_keys: evento.target.checked }))
                }
              />
              {t("settings.safeProxy.config.apiKeys")}
            </label>
            <p className="nota" style={{ margin: "0 0 8px" }}>
              {t("settings.safeProxy.config.apiKeys.hint")}
            </p>

            <label className="opcion">
              <input
                type="checkbox"
                checked={proxyForm.obfuscate_python}
                onChange={(evento) =>
                  setProxyForm((previo) => ({
                    ...previo,
                    obfuscate_python: evento.target.checked,
                  }))
                }
              />
              {t("settings.safeProxy.config.obfuscate")}
            </label>
            <label className="opcion">
              <input
                type="checkbox"
                checked={proxyForm.strip_comments}
                onChange={(evento) =>
                  setProxyForm((previo) => ({ ...previo, strip_comments: evento.target.checked }))
                }
              />
              {t("settings.safeProxy.config.stripComments")}
            </label>
            <label className="opcion">
              <input
                type="checkbox"
                checked={proxyForm.fail_closed}
                onChange={(evento) =>
                  setProxyForm((previo) => ({ ...previo, fail_closed: evento.target.checked }))
                }
              />
              {t("settings.safeProxy.config.failClosed")}
            </label>

            <div className="acciones">
              <button className="primary" onClick={() => void guardarProxyConfig()}>
                {t("settings.safeProxy.config.save")}
              </button>
            </div>
          </div>
        )}
      </section>
    );
  }

  function paginaMantenimiento() {
    return (
      <section className="tarjeta">
        <div className="acciones" style={{ marginTop: 0 }}>
          <button onClick={() => void ejecutar("/doctor", "doctor")}>
            {t("settings.maintenance.doctor")}
          </button>
          <button onClick={() => void ejecutar("/stats", "stats")}>
            {t("settings.maintenance.stats")}
          </button>
          <button onClick={() => void ejecutar("/setup", "setup (sandbox)", { metodo: "POST" })}>
            {t("settings.permissions.setup")}
          </button>
          <button
            onClick={() =>
              void ejecutar("/compact", t("settings.maintenance.compact"), { metodo: "POST" })
            }
          >
            {t("settings.maintenance.compact")}
          </button>
          <button onClick={() => void ejecutar("/init", "init", { metodo: "POST" })}>
            {t("settings.maintenance.init")}
          </button>
          <button onClick={() => void cargarHerramientas()}>{t("settings.maintenance.tools")}</button>
          <button onClick={() => void cargarSnapshots()}>{t("settings.maintenance.snapshots")}</button>
        </div>

        {salida && <pre className="salida">{salida}</pre>}

        {herramientas && (
          <div className="subbloque">
            <p className="etiqueta">
              {t("settings.maintenance.tools.title", { n: herramientas.length })}
            </p>
            {herramientas.length === 0 && (
              <div className="vacio">{t("settings.maintenance.tools.empty")}</div>
            )}
            {herramientas.map((tool) => (
              <div key={tool.id} className="ficha" style={{ cursor: "default" }}>
                <h3>
                  {tool.id}
                  {tool.reserved && <span className="chip acento">kernel</span>}
                </h3>
                <div className="sub">
                  {tool.categories?.length
                    ? tool.categories.join(" · ")
                    : t("settings.maintenance.tools.noCategories")}
                </div>
                {tool.description && <div className="nota">{tool.description}</div>}
              </div>
            ))}
          </div>
        )}

        {instantaneas && (
          <div className="subbloque">
            <p className="etiqueta">
              {t("settings.maintenance.snapshots.title", { n: instantaneas.length })}
            </p>
            {instantaneas.length === 0 && (
              <div className="vacio">{t("settings.maintenance.snapshots.empty")}</div>
            )}
            {instantaneas.map((nombreSnapshot) => (
              <div key={nombreSnapshot} className="item" style={{ cursor: "default" }}>
                <span className="nombre">{nombreSnapshot}</span>
                <button className="quiet" onClick={() => restaurarSnapshot(nombreSnapshot)}>
                  {t("settings.maintenance.restore")}
                </button>
              </div>
            ))}
          </div>
        )}

        <p className="nota">{t("settings.maintenance.restore.note")}</p>

        <div className="acciones">
          <button
            className="peligro"
            onClick={() =>
              props.onConfirmar(
                t("settings.maintenance.reset.title"),
                t("settings.maintenance.reset.detail"),
                () =>
                  void ejecutar("/reset", "reset", {
                    metodo: "POST",
                    cuerpo: { keep_skills: true, confirm: true },
                  }),
              )
            }
          >
            {t("settings.maintenance.reset")}
          </button>
        </div>
      </section>
    );
  }

  function paginaAvanzado() {
    return (
      <section className="tarjeta">
        <pre className="salida">{JSON.stringify(props.config.config, null, 2)}</pre>
        <p className="nota">
          {t("settings.advanced.note.a")}
          <code>api_key_set</code>
          {t("settings.advanced.note.b")}
        </p>
      </section>
    );
  }

  function paginaApariencia() {
    const actual = THEMES.find((item) => item.id === props.tema) ?? THEMES[0];
    const nivelActual =
      TEXT_INTENSITIES.find((nivel) => nivel.id === props.intensidad) ?? TEXT_INTENSITIES[1];
    return (
      <section className="tarjeta">
        <p className="nota" style={{ marginTop: 0 }}>
          {t("settings.appearance.note")}
        </p>
        <div className="subbloque" style={{ marginTop: 0 }}>
          <p className="etiqueta" style={{ marginTop: 0 }}>
            {t("settings.language")}
          </p>
          <label className="campo" style={{ marginBottom: 0 }}>
            <span>{t("settings.language")}</span>
            <select
              value={props.idioma}
              aria-label={t("settings.language")}
              onChange={(evento) => {
                const valor = evento.target.value;
                if (isIdioma(valor)) props.onIdioma(valor);
              }}
            >
              {IDIOMAS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <span className="nota" style={{ margin: 0 }}>
              {t("settings.language.note")}
            </span>
          </label>
        </div>
        <ul className="temas-grid" aria-label={t("settings.appearance.themes.aria")}>
          {THEMES.map((tema) => {
            const activo = tema.id === props.tema;
            return (
              <li key={tema.id}>
                <button
                  type="button"
                  className={`tema${activo ? " activo" : ""}`}
                  aria-pressed={activo}
                  onClick={() => props.onTema(tema.id)}
                >
                  <span
                    className="tema-preview"
                    style={{ background: tema.swatches.background }}
                    aria-hidden="true"
                  >
                    <span
                      className="tema-preview-panel"
                      style={{ background: tema.swatches.panel }}
                    />
                    <span
                      className="tema-preview-barra"
                      style={{ background: tema.swatches.text }}
                    />
                    <span
                      className="tema-preview-acento"
                      style={{ background: tema.swatches.accent }}
                    />
                  </span>
                  <span className="tema-texto">
                    <span className="tema-nombre">
                      {t(CLAVES_TEMA[tema.id].nombre)}
                      <span className="tema-modo">
                        {tema.light
                          ? t("settings.appearance.light")
                          : t("settings.appearance.dark")}
                      </span>
                    </span>
                    <span className="tema-desc">{t(CLAVES_TEMA[tema.id].descripcion)}</span>
                  </span>
                  {activo && <span className="chip acento">{t("settings.models.active")}</span>}
                </button>
                {tema.id === "deepdark" && activo && (
                  <label className="tema-intensidad">
                    <span className="tema-intensidad-titulo">
                      {t("settings.appearance.intensity")}
                    </span>
                    <select
                      value={props.intensidad}
                      aria-label={t("settings.appearance.intensity.aria")}
                      onChange={(evento) => {
                        if (isTextIntensity(evento.target.value)) {
                          props.onIntensidad(evento.target.value);
                        }
                      }}
                    >
                      {TEXT_INTENSITIES.map((nivel) => (
                        <option key={nivel.id} value={nivel.id}>
                          {t(CLAVES_INTENSIDAD[nivel.id].label)} —{" "}
                          {t(CLAVES_INTENSIDAD[nivel.id].hint)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </li>
            );
          })}
        </ul>
        <div className="subbloque" style={{ marginTop: "var(--s4)" }}>
          <p className="etiqueta" style={{ marginTop: 0 }}>
            {t("settings.appearance.watermark")}
          </p>
          <label className="opcion">
            <input
              type="checkbox"
              checked={props.marca}
              onChange={(evento) => props.onMarca(evento.target.checked)}
            />
            {t("settings.appearance.watermark.show")}
          </label>
          <label className="campo" style={{ marginTop: 8, marginBottom: 0 }}>
            <span>{t("settings.appearance.watermark.opacity")}</span>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <input
                type="range"
                min={Math.round(CHAT_WATERMARK_OPACITY_MIN * 100)}
                max={Math.round(CHAT_WATERMARK_OPACITY_MAX * 100)}
                step={Math.round(CHAT_WATERMARK_OPACITY_STEP * 100)}
                value={Math.round(props.marcaOpacidad * 100)}
                disabled={!props.marca}
                aria-label={t("settings.appearance.watermark.opacity.aria")}
                aria-valuetext={`${Math.round(props.marcaOpacidad * 100)}%`}
                onChange={(evento) =>
                  props.onMarcaOpacidad(Number(evento.target.value) / 100)
                }
              />
              <span className="chip" aria-hidden="true">
                {Math.round(props.marcaOpacidad * 100)}%
              </span>
            </div>
            <span className="nota" style={{ margin: 0 }}>
              {props.marca
                ? t("settings.appearance.watermark.applied")
                : t("settings.appearance.watermark.disabled")}
            </span>
          </label>
        </div>
        <p className="nota">
          {t("settings.appearance.current", { tema: t(CLAVES_TEMA[actual.id].nombre) })}
          {actual.id === "deepdark"
            ? t("settings.appearance.current.intensity", {
                nivel: t(CLAVES_INTENSIDAD[nivelActual.id].label),
              })
            : ""}
          {t("settings.appearance.current.note")}
        </p>
      </section>
    );
  }

  return (
    <div className="pantalla">
      <header className="pantalla-cabecera">
        {props.seccion ? (
          <button
            className="quiet"
            onClick={() => props.onSeccion(null)}
            title={t("settings.header.back.title")}
          >
            {t("settings.header.back")}
          </button>
        ) : (
          <button
            className="quiet"
            onClick={props.onCerrar}
            title={t("settings.header.close.title")}
          >
            {t("settings.header.close")}
          </button>
        )}
        <h2>{props.seccion ? titulo : t("settings.title")}</h2>
        {props.seccion ? (
          <nav className="migas" aria-label={t("settings.breadcrumb.aria")}>
            <button className="quiet" onClick={() => props.onSeccion(null)}>
              {t("settings.title")}
            </button>
            <span aria-hidden="true">/</span>
            <span aria-current="page">{titulo}</span>
          </nav>
        ) : (
          <span className="nota">{t("settings.subtitle")}</span>
        )}
      </header>

      <div className="pantalla-cuerpo">
        {props.seccion === null ? (
          <ul className="ajustes-grid" aria-label={t("settings.categories.aria")}>
            {CATEGORIAS.map((categoria) => (
              <li key={categoria.id}>
                <button
                  type="button"
                  className="categoria"
                  onClick={() => props.onSeccion(categoria.id)}
                >
                  <span className="categoria-titulo">{t(categoria.clave)}</span>
                  <span className="categoria-resumen">{t(categoria.resumenClave)}</span>
                  <span className="categoria-meta">{metaDe(categoria.id)}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="ajustes-pagina">
            {props.seccion === "modelos" && paginaModelos()}
            {props.seccion === "roles" && paginaRoles()}
            {props.seccion === "permisos" && paginaPermisos()}
            {props.seccion === "escritorio" && paginaEscritorio()}
            {props.seccion === "safe-proxy" && paginaSafeProxy()}
            {props.seccion === "mantenimiento" && paginaMantenimiento()}
            {props.seccion === "avanzado" && paginaAvanzado()}
            {props.seccion === "apariencia" && paginaApariencia()}
          </div>
        )}
      </div>
    </div>
  );
}

interface FilaProps {
  rol: string;
  /** Proveedor/modelo/esfuerzo configurados para este rol (según el backend). */
  actual: {
    provider?: string;
    model?: string;
    reasoning_effort?: string;
    think?: boolean | null;
    effort_control?: string;
  };
  /** Proveedores guardados (todos: también los añadidos a mano). */
  proveedores: SavedProviderView[];
  /** Id del proveedor global (para la etiqueta del fallback). */
  global: string;
  onAplicar: (
    proveedor: string,
    model: string,
    esfuerzo: string,
    think: boolean | null,
    niveles: string[],
    control: string,
  ) => void;
}

function FilaRol(props: FilaProps) {
  const t = useT();
  const [proveedor, setProveedor] = useState(props.actual.provider ?? "");
  const [model, setModel] = useState(props.actual.model ?? "");
  const [esfuerzo, setEsfuerzo] = useState(props.actual.reasoning_effort || "auto");
  const [think, setThink] = useState(props.actual.think ?? true);
  const [recarga, setRecarga] = useState(0);
  const [modelos, setModelos] = useState<ModelsResult | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState("");
  // Proveedor del que salió el `model` actual: sólo se invalida si el proveedor **cambia**.
  const anterior = useRef(props.actual.provider ?? "");

  // El backend manda: si su vista cambia (tras aplicar o al recargar), se sincroniza.
  useEffect(() => {
    setProveedor(props.actual.provider ?? "");
    setModel(props.actual.model ?? "");
    setEsfuerzo(props.actual.reasoning_effort || "auto");
    setThink(props.actual.think ?? true);
  }, [
    props.actual.provider,
    props.actual.model,
    props.actual.reasoning_effort,
    props.actual.think,
  ]);

  // Al elegir un proveedor (o al montar con uno ya fijado) se traen sus modelos con la clave
  // guardada en el servidor: la clave nunca llega al renderer.
  useEffect(() => {
    if (!proveedor) {
      setModelos(null);
      setError("");
      anterior.current = "";
      return;
    }
    let vigente = true;
    setCargando(true);
    setError("");
    void (async () => {
      try {
        const datos = await api<ModelsResult>(`/models?provider=${encodeURIComponent(proveedor)}`);
        if (!vigente) return;
        setModelos(datos);
        if (!datos.ok) setError(datos.error || t("settings.roles.modelsError"));
        const cambio = anterior.current !== proveedor;
        anterior.current = proveedor;
        // Si cambió el proveedor y el modelo anterior no es suyo, se invalida (no se
        // arrastra un modelo de otro proveedor por descuido). Si no se pudieron listar, se
        // conserva y se avisa: no se borra a ciegas.
        setModel((previo) =>
          cambio && datos.ok && previo && !datos.models.includes(previo) ? "" : previo,
        );
      } catch (exc) {
        if (!vigente) return;
        setModelos(null);
        setError(mensajeDe(exc));
      } finally {
        if (vigente) setCargando(false);
      }
    })();
    return () => {
      vigente = false;
    };
  }, [proveedor, recarga]);

  const elegido = props.proveedores.find((item) => item.id === proveedor) ?? null;
  const global = props.proveedores.find((item) => item.id === props.global) ?? null;
  const opciones = modelos?.ok ? modelos.models : [];
  // Niveles del modelo elegido: catálogo de capacidades + lo que anunció la API (ya calculado
  // en el backend). Vacío = este modelo no lo admite y el control se desactiva.
  const niveles = (model ? modelos?.effort_options?.[model] : undefined) ?? [];
  const anunciados =
    (model ? modelos?.capabilities?.[model]?.effort?.supported_levels : undefined) ?? [];
  // Canal de los niveles anunciados (p. ej. `reasoning_effort`): viaja con el rol para que el
  // adapter envíe el parámetro correcto y nunca uno incompatible con el modelo.
  const meta = (model ? modelos?.effort_meta?.[model] : undefined) ?? null;
  const control =
    meta?.control ||
    (model ? modelos?.capabilities?.[model]?.effort?.control : undefined) ||
    (model ? modelos?.capabilities?.[model]?.control : undefined) ||
    "";
  const claveNiveles = niveles.join(",");

  // Si cambia el modelo y el esfuerzo guardado no es de ese modelo, se vuelve a «auto».
  const anteriorModelo = useRef(model);
  useEffect(() => {
    const cambio = anteriorModelo.current !== model;
    anteriorModelo.current = model;
    setEsfuerzo((previo) =>
      cambio && previo !== "auto" && !niveles.includes(previo) ? "auto" : previo,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, claveNiveles]);

  return (
    <div className="fila-rol">
      <div className="rejilla" style={{ marginTop: 8, alignItems: "end" }}>
        <div className="campo" style={{ marginBottom: 0 }}>
          <label>
            {props.rol}
            {props.actual.provider ? "" : t("settings.roles.row.usesGlobal")}
            {props.actual.provider && esfuerzo !== "auto"
              ? t("settings.roles.row.effort", { esfuerzo })
              : ""}
          </label>
          <select value={proveedor} onChange={(evento) => setProveedor(evento.target.value)}>
            <option value="">
              {t("settings.roles.row.global", {
                proveedor: global?.name || t("settings.models.none"),
              })}
            </option>
            {props.proveedores.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.model || item.id}
              </option>
            ))}
          </select>
        </div>
        <div
          className="campo"
          style={{
            marginBottom: 0,
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          {opciones.length > 0 ? (
            <select value={model} onChange={(evento) => setModel(evento.target.value)}>
              <option value="">
                {elegido?.model
                  ? t("settings.roles.row.providerDefault.model", { modelo: elegido.model })
                  : t("settings.roles.row.providerDefault")}
              </option>
              {model !== "" && !opciones.includes(model) && <option value={model}>{model}</option>}
              {opciones.map((item) => (
                <option key={item} value={item}>
                  {item}
                  {item === modelos?.suggested ? t("common.mostUsed") : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={model}
              onChange={(evento) => setModel(evento.target.value)}
              placeholder={
                cargando ? t("settings.models.loading") : t("settings.roles.row.modelPlaceholder")
              }
            />
          )}
          <button
            className="quiet"
            onClick={() => setRecarga((valor) => valor + 1)}
            disabled={cargando || !proveedor}
            title={t("settings.roles.row.refresh.title")}
            aria-label={t("settings.roles.row.refresh.aria")}
          >
            {cargando ? "…" : "↻"}
          </button>
          {/* Think y esfuerzo, junto al modelo elegido (el picker del rol). */}
          <label
            className="opcion"
            title={
              meta?.think_param
                ? t("settings.roles.row.think.title", { canal: meta.think_param })
                : t("settings.roles.row.think.none")
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
          {niveles.length > 0 ? (
            <select
              value={esfuerzo}
              title={t("settings.roles.row.effort.title")}
              aria-label={t("settings.roles.row.effort.aria", { rol: props.rol })}
              onChange={(evento) => setEsfuerzo(evento.target.value)}
            >
              {niveles.map((nivel) => (
                <option key={nivel} value={nivel}>
                  {nivel}
                </option>
              ))}
              {esfuerzo !== "auto" && !niveles.includes(esfuerzo) && (
                <option value={esfuerzo}>{esfuerzo}</option>
              )}
            </select>
          ) : (
            <span className="nota" style={{ margin: 0 }}>
              {cargando
                ? t("settings.roles.row.levels.loading")
                : t("settings.roles.row.levels.none")}
            </span>
          )}
          <button
            onClick={() =>
              props.onAplicar(
                proveedor,
                model,
                esfuerzo,
                meta?.think_param ? think : null,
                anunciados,
                control,
              )
            }
          >
            {t("settings.roles.apply")}
          </button>
        </div>
      </div>

      {proveedor && meta && (meta.control || meta.inferred || meta.think_param) && (
        <span className="nota" style={{ margin: "6px 0 0" }}>
          {meta.think_param ? `Think: ${meta.think_param}` : ""}
          {meta.control
            ? `${meta.think_param ? " · " : ""}${t("settings.roles.row.channel", {
                canal: meta.control,
              })}`
            : ""}
          {meta.inferred ? t("settings.roles.row.inferred") : ""}
        </span>
      )}

      {proveedor && (cargando || error) && (
        <p className="nota">
          {cargando
            ? t("settings.models.loading")
            : t("settings.roles.row.modelsError", { error })}
        </p>
      )}
    </div>
  );
}
