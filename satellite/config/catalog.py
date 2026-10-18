"""Catálogo de proveedores conocidos con su ``base_url`` ya puesta.

La idea: que el usuario sólo pegue su API key. La ``base_url`` se muestra y se
puede editar en el asistente antes de guardar; si está mal, ``Cargar modelos``
falla **diciendo la URL exacta que probó** (no hay configuración silenciosa).

Las familias que se exponen son dos: ``openai`` (todo lo compatible con la API
de OpenAI) y ``anthropic``. Es una agrupación de *protocolo*, no comercial.
"""

from __future__ import annotations

from dataclasses import dataclass

OPENAI = "openai"
ANTHROPIC = "anthropic"
FAMILIES: tuple[str, ...] = (OPENAI, ANTHROPIC)


@dataclass(frozen=True)
class CatalogEntry:
    """Entrada del catálogo: clave, etiqueta, familia y URL base."""

    key: str
    label: str
    family: str
    base_url: str
    group: str = "API"
    note: str = ""
    needs_edit: bool = False
    context_window: int = 0  # 0 = desconocida (se estima por modelo o por defecto)

    @property
    def family_label(self) -> str:
        """Etiqueta de la familia (lo que se guarda como ``name``)."""
        return "Anthropic" if self.family == ANTHROPIC else "OpenAI-compatible"

    @property
    def needs_key(self) -> bool:
        """¿Hace falta API key? Los proveedores locales no la piden."""
        return self.group != "Local"


GROUPS: tuple[str, ...] = ("API", "Asia", "Local", "Cloud (editar URL)", "Pasarelas")

CATALOG: tuple[CatalogEntry, ...] = (
    # --- API internacional ------------------------------------------------
    CatalogEntry("openai", "OpenAI", OPENAI, "https://api.openai.com/v1"),
    CatalogEntry("anthropic", "Anthropic (Claude)", ANTHROPIC, "https://api.anthropic.com"),
    CatalogEntry("gemini", "Google Gemini (modo compatible)", OPENAI, "https://generativelanguage.googleapis.com/v1beta/openai"),
    CatalogEntry("xai", "xAI (Grok)", OPENAI, "https://api.x.ai/v1"),
    CatalogEntry("deepseek", "DeepSeek", OPENAI, "https://api.deepseek.com/v1"),
    CatalogEntry("mistral", "Mistral AI", OPENAI, "https://api.mistral.ai/v1"),
    CatalogEntry("groq", "Groq", OPENAI, "https://api.groq.com/openai/v1"),
    CatalogEntry("together", "Together AI", OPENAI, "https://api.together.xyz/v1"),
    CatalogEntry("fireworks", "Fireworks AI", OPENAI, "https://api.fireworks.ai/inference/v1"),
    CatalogEntry("openrouter", "OpenRouter", OPENAI, "https://openrouter.ai/api/v1"),
    CatalogEntry("perplexity", "Perplexity", OPENAI, "https://api.perplexity.ai"),
    CatalogEntry("cohere", "Cohere (modo compatible)", OPENAI, "https://api.cohere.ai/compatibility/v1"),
    CatalogEntry("cerebras", "Cerebras", OPENAI, "https://api.cerebras.ai/v1"),
    CatalogEntry("sambanova", "SambaNova", OPENAI, "https://api.sambanova.ai/v1"),
    CatalogEntry("hyperbolic", "Hyperbolic", OPENAI, "https://api.hyperbolic.xyz/v1"),
    CatalogEntry("novita", "Novita AI", OPENAI, "https://api.novita.ai/v3/openai"),
    CatalogEntry("deepinfra", "DeepInfra", OPENAI, "https://api.deepinfra.com/v1/openai"),
    CatalogEntry("nvidia", "NVIDIA NIM", OPENAI, "https://integrate.api.nvidia.com/v1"),
    CatalogEntry("github", "GitHub Models", OPENAI, "https://models.github.ai/inference"),
    CatalogEntry("baseten", "Baseten", OPENAI, "https://inference.baseten.co/v1"),
    CatalogEntry("nebius", "Nebius AI Studio", OPENAI, "https://api.studio.nebius.ai/v1"),
    CatalogEntry("lambda", "Lambda", OPENAI, "https://api.lambdalabs.com/v1"),
    CatalogEntry("featherless", "Featherless AI", OPENAI, "https://api.featherless.ai/v1"),
    CatalogEntry("nscale", "Nscale", OPENAI, "https://inference.api.nscale.com/v1"),
    CatalogEntry("chutes", "Chutes", OPENAI, "https://llm.chutes.ai/v1"),
    CatalogEntry("huggingface", "Hugging Face (router)", OPENAI, "https://router.huggingface.co/v1"),
    CatalogEntry("replicate", "Replicate", OPENAI, "https://api.replicate.com/v1"),
    CatalogEntry("anyscale", "Anyscale", OPENAI, "https://api.endpoints.anyscale.com/v1"),
    CatalogEntry("ai21", "AI21 Labs", OPENAI, "https://api.ai21.com/studio/v1"),
    CatalogEntry("upstage", "Upstage", OPENAI, "https://api.upstage.ai/v1/solar"),
    CatalogEntry("vercel", "Vercel AI Gateway", OPENAI, "https://ai-gateway.vercel.sh/v1"),

    # --- Asia -------------------------------------------------------------
    CatalogEntry("qwen", "Alibaba Model Studio (Qwen)", OPENAI, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", group="Asia"),
    CatalogEntry("moonshot", "Moonshot (Kimi)", OPENAI, "https://api.moonshot.cn/v1", group="Asia"),
    CatalogEntry("zhipu", "Z.ai (GLM)", OPENAI, "https://api.z.ai/api/paas/v4", group="Asia"),
    CatalogEntry("minimax", "MiniMax", OPENAI, "https://api.minimax.chat/v1", group="Asia"),
    CatalogEntry("yi", "01.AI (Yi)", OPENAI, "https://api.lingyiwanwu.com/v1", group="Asia"),
    CatalogEntry("siliconflow", "SiliconFlow", OPENAI, "https://api.siliconflow.cn/v1", group="Asia"),
    CatalogEntry("stepfun", "StepFun", OPENAI, "https://api.stepfun.com/v1", group="Asia"),
    CatalogEntry("qianfan", "Baidu Qianfan", OPENAI, "https://qianfan.baidubce.com/v2", group="Asia"),
    CatalogEntry("hunyuan", "Tencent Hunyuan", OPENAI, "https://api.hunyuan.cloud.tencent.com/v1", group="Asia"),
    CatalogEntry("spark", "iFlytek Spark", OPENAI, "https://spark-api-open.xf-yun.com/v1", group="Asia"),
    CatalogEntry("doubao", "Volcengine Ark (Doubao)", OPENAI, "https://ark.cn-beijing.volces.com/api/v3", group="Asia"),
    CatalogEntry("tiangong", "Tiangong (Kunlun)", OPENAI, "https://api.singularity-ai.com/v1", group="Asia"),

    # --- Local ------------------------------------------------------------
    CatalogEntry("ollama", "Ollama (local, sin clave)", OPENAI, "http://localhost:11434/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("lmstudio", "LM Studio (local, sin clave)", OPENAI, "http://localhost:1234/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("llamacpp", "llama.cpp server (local, sin clave)", OPENAI, "http://localhost:8080/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("vllm", "vLLM (local, sin clave)", OPENAI, "http://localhost:8000/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("localai", "LocalAI (local, sin clave)", OPENAI, "http://localhost:8080/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("jan", "Jan (local, sin clave)", OPENAI, "http://localhost:1337/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("llamafile", "Llamafile (local, sin clave)", OPENAI, "http://localhost:8080/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("koboldcpp", "KoboldCpp (local, sin clave)", OPENAI, "http://localhost:5001/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("textgen", "Text Generation WebUI (local)", OPENAI, "http://localhost:5000/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("gpt4all", "GPT4All (local, sin clave)", OPENAI, "http://localhost:4891/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("docker", "Docker Model Runner (local)", OPENAI, "http://localhost:12434/engines/v1", group="Local", note="no necesita API key", context_window=32_768),
    CatalogEntry("azure", "Azure OpenAI (necesita tu recurso)", OPENAI, "https://<recurso>.openai.azure.com/openai/v1", group="Cloud (editar URL)", needs_edit=True),
    CatalogEntry("cloudflare", "Cloudflare Workers AI (necesita account_id)", OPENAI, "https://api.cloudflare.com/client/v4/accounts/<account_id>/ai/v1", group="Cloud (editar URL)", needs_edit=True),
    CatalogEntry("lepton", "Lepton (necesita subdominio)", OPENAI, "https://<modelo>.lepton.run/api/v1", group="Cloud (editar URL)", needs_edit=True),

    # --- Pasarelas (al final de la lista) ---------------------------------
    CatalogEntry(
        "commandcode",
        "CommandCode (OpenAI-compatible)",
        OPENAI,
        "https://api.commandcode.ai/provider/v1",
        group="Pasarelas",
    ),
    CatalogEntry(
        "commandcode-anthropic",
        "CommandCode (Claude / Anthropic)",
        ANTHROPIC,
        "https://api.commandcode.ai/provider",
        group="Pasarelas",
    ),
)

MANUAL_KEY = "manual"
MANUAL_LABEL = "Otro OpenAI-compatible (URL manual)"

_BY_KEY = {entry.key: entry for entry in CATALOG}


def get(key: str) -> CatalogEntry | None:
    """Entrada por clave exacta."""
    return _BY_KEY.get(str(key).strip().lower())


def key_for_base_url(base_url: str) -> str:
    """Clave del catálogo cuya ``base_url`` coincide (``""`` si no es del catálogo).

    Sirve para reconocer el proveedor de un guardado, que sólo conserva la familia
    (``openai``/``anthropic``) y la URL, y así elegir su estrategia de listado de modelos.
    """
    wanted = str(base_url or "").strip().rstrip("/").lower()
    if not wanted:
        return ""
    for entry in CATALOG:
        if entry.base_url.strip().rstrip("/").lower() == wanted:
            return entry.key
    return ""


def find(text: str) -> list[CatalogEntry]:
    """Entradas cuya clave, etiqueta o grupo contienen ``text`` (vacío = todas)."""
    needle = str(text).strip().lower()
    if not needle:
        return list(CATALOG)
    return [
        entry
        for entry in CATALOG
        if needle in entry.key
        or needle in entry.label.lower()
        or needle in entry.group.lower()
        or needle in entry.base_url.lower()
    ]


def by_group() -> dict[str, list[CatalogEntry]]:
    """Entradas agrupadas (en el orden de :data:`GROUPS`)."""
    grouped: dict[str, list[CatalogEntry]] = {group: [] for group in GROUPS}
    for entry in CATALOG:
        grouped.setdefault(entry.group, []).append(entry)
    return grouped


def keys() -> list[str]:
    """Todas las claves del catálogo."""
    return [entry.key for entry in CATALOG]
