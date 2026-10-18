"""Saneador del proxy (vendorizado desde ``safe_proxy.py``).

Lógica **pura** (sin FastAPI ni ficheros): detectores de secretos/PII, ofuscación de
Python y el pipeline de saneado. Se parametriza con un dict de config (``policy``/``code``)
y un :class:`Vault` por petición, así que es seguro instanciarlo por servicio y por test.

Origen: ``safe-proxy/safe_proxy.py`` (copia del autor, sin licencia declarada; ver
``NOTICE.md``). No se importa nada del repositorio hermano: este módulo es autónomo.
"""

from __future__ import annotations

import ast
import builtins
import io
import keyword
import re
import tokenize

# --------------------------------------------------------------------------- #
# Detección
# --------------------------------------------------------------------------- #
# --- Claves de API: familia propia, apagable con ``policy.api_keys`` ------------------ #
# Se detectan y se tratan como cualquier secreto (bloquear o enmascarar, según
# ``policy.secrets_action``). Su contenido **nunca** se registra: sólo conteos y hashes.
API_KEY_PATTERNS = [
    ("API_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),              # OpenAI/DeepSeek/Anthropic
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),       # Google Cloud / Gemini
    ("XAI_API_KEY", re.compile(r"\bxai-[A-Za-z0-9]{20,}\b")),           # xAI
    ("HUGGINGFACE_TOKEN", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),      # Hugging Face
    ("GROQ_API_KEY", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),          # Groq
    ("REPLICATE_TOKEN", re.compile(r"\br8_[A-Za-z0-9]{20,}\b")),        # Replicate
    ("PERPLEXITY_API_KEY", re.compile(r"\bpplx-[A-Za-z0-9]{20,}\b")),   # Perplexity
]

# --- Otros secretos: **no** son claves de API y siguen siempre activos ---------------- #
OTHER_SECRET_PATTERNS = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "CREDENCIAL",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|passwd|password|token|authorization)\b"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=.]{8,}"
        ),
    ),
]


def secret_patterns(api_keys: bool = True) -> list[tuple[str, "re.Pattern[str]"]]:
    """Reglas de secretos activas: la familia de claves de API se puede apagar aparte.

    Se conserva el orden original (lo más específico primero); al apagar ``api_keys`` sólo
    desaparece esa familia: el resto de reglas de secretos siguen aplicándose.
    """
    if api_keys:
        return [
            *OTHER_SECRET_PATTERNS[:2],
            *API_KEY_PATTERNS,
            *OTHER_SECRET_PATTERNS[2:],
        ]
    return list(OTHER_SECRET_PATTERNS)


# Compatibilidad: la lista por defecto (familia de claves de API incluida).
SECRET_PATTERNS = secret_patterns(True)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _valid_card(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def _valid_ip(s: str) -> bool:
    return all(0 <= int(p) <= 255 for p in s.split("."))


# El orden importa: lo más específico primero.
PII_PATTERNS = [
    ("CLABE", re.compile(r"(?<!\d)\d{18}(?!\d)"), None),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), _valid_card),
    (
        "CURP",
        re.compile(r"\b[A-Z]{4}\d{6}[HMX][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[A-Z0-9]\d\b", re.I),
        None,
    ),
    ("RFC", re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b", re.I), None),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), None),
    (
        "PHONE",
        re.compile(
            r"(?<![\d.])(?:\+?52[\s.-]?)?"
            r"(?:\d{2}[\s.-]?\d{4}[\s.-]?\d{4}|\d{3}[\s.-]?\d{3}[\s.-]?\d{4})(?!\d)"
        ),
        None,
    ),
    ("IP", re.compile(r"(?<!\d)(?<!\d\.)(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)"), _valid_ip),
]


class Blocked(Exception):
    """La petición viola la política; no se envía nada."""

    def __init__(self, reason: str, category: str):
        super().__init__(reason)
        self.reason = reason
        self.category = category


# --------------------------------------------------------------------------- #
# Vault: mapa reversible marcador <-> original (vive sólo durante la petición)
# --------------------------------------------------------------------------- #
PLACEHOLDER_RE = re.compile(r"<[A-Z]+_\d+>|\bobf_\d+\b")


class Vault:
    def __init__(self) -> None:
        self.forward: dict[tuple[str, str], str] = {}
        self.reverse: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.stats: dict[str, int] = {}

    def token(self, kind: str, original: str, norm: str | None = None) -> str:
        key = (kind, norm if norm is not None else original)
        if key in self.forward:
            return self.forward[key]
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        placeholder = f"obf_{n}" if kind == "ID" else f"<{kind}_{n}>"
        self.forward[key] = placeholder
        self.reverse[placeholder] = original
        return placeholder

    def count(self, kind: str) -> None:
        self.stats[kind] = self.stats.get(kind, 0) + 1

    def restore(self, text: str) -> str:
        if not self.reverse:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self.reverse.get(m.group(0), m.group(0)), text)


def restore_obj(obj, vault: Vault):
    if isinstance(obj, str):
        return vault.restore(obj)
    if isinstance(obj, list):
        return [restore_obj(x, vault) for x in obj]
    if isinstance(obj, dict):
        return {k: restore_obj(v, vault) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- #
# Ofuscación de código Python
# --------------------------------------------------------------------------- #
PROTECTED = set(dir(builtins)) | set(keyword.kwlist) | set(keyword.softkwlist) | {"self", "cls", "_"}


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def collect_defined(source: str) -> set[str]:
    """Nombres definidos por el usuario (funciones, clases, args, variables, self.attr)."""
    tree = ast.parse(source)
    defined: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        if isinstance(node, ast.arg):
            defined.add(node.arg)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
        ):
            defined.add(node.attr)
        if isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            defined.update(node.names)
        if isinstance(node, ast.Import):
            for al in node.names:
                imported.add((al.asname or al.name).split(".")[0])
        if isinstance(node, ast.ImportFrom):
            for al in node.names:
                imported.add(al.asname or al.name)
    return {n for n in defined - imported - PROTECTED if not _is_dunder(n)}


def collect_defined_fallback(code: str) -> set[str]:
    """Para fragmentos que no parsean (p. ej. código parcial en una respuesta previa)."""
    names: set[str] = set()
    for m in re.finditer(r"\b(?:def|class)\s+([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?", code):
        names.add(m.group(1))
        for p in (m.group(2) or "").split(","):
            p = re.split(r"[:=]", p, maxsplit=1)[0].strip().lstrip("*")
            if p.isidentifier():
                names.add(p)
    for m in re.finditer(r"^\s*(?:self\.)?([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)", code, re.M):
        names.add(m.group(1))
    for m in re.finditer(r"\bfor\s+([A-Za-z_]\w*)\s+in\b", code):
        names.add(m.group(1))
    return {n for n in names - PROTECTED if not _is_dunder(n)}


def obfuscate_python(source: str, names: set[str], vault: Vault, strip_comments: bool = True) -> str:
    lines = io.StringIO(source).readlines()
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def pos(rc: tuple[int, int]) -> int:
        return starts[rc[0] - 1] + rc[1]

    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    edits: list[tuple[int, int, str]] = []

    for t in tokens:
        if t.type == tokenize.COMMENT and strip_comments:
            edits.append((pos(t.start), pos(t.end), ""))
        elif t.type == tokenize.NAME and t.string in names:
            edits.append((pos(t.start), pos(t.end), vault.token("ID", t.string)))

    # Docstrings / cadenas sueltas: una sentencia formada sólo por un STRING.
    sig = [t for t in tokens if t.type not in (tokenize.NL, tokenize.COMMENT, tokenize.ENCODING)]
    stmt_edge = (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    for j, t in enumerate(sig):
        if t.type != tokenize.STRING:
            continue
        prev_t = sig[j - 1].type if j > 0 else tokenize.NEWLINE
        next_t = sig[j + 1].type if j + 1 < len(sig) else tokenize.NEWLINE
        if prev_t in stmt_edge and next_t in (tokenize.NEWLINE, tokenize.ENDMARKER):
            edits.append((pos(t.start), pos(t.end), '"..."'))

    for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        source = source[:start] + rep + source[end:]
    return source


def looks_like_python(code: str) -> bool:
    if len(code.strip()) < 20:
        return False
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return False
    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in ast.walk(tree)):
        return True
    return len(tree.body) >= 3 and any(isinstance(n, (ast.Assign, ast.Import, ast.ImportFrom)) for n in tree.body)


# --------------------------------------------------------------------------- #
# Sanitizer
# --------------------------------------------------------------------------- #
FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
PY_LANGS = {"python", "py", "python3"}
TEXT_LANGS = {"", "text", "txt", "log", "json", "yaml", "yml", "csv", "md", "markdown", "console", "output"}


class Sanitizer:
    def __init__(self, cfg: dict):
        pol = cfg.get("policy", {})
        self.code = cfg.get("code", {})
        self.block_terms = [t.lower() for t in (pol.get("block_terms") or [])]
        self.block_regex = [re.compile(r) for r in (pol.get("block_regex") or [])]
        self.secrets_action = pol.get("secrets_action", "block")
        # ``policy.api_keys``: la familia de claves de API se puede apagar por separado.
        # Fail-safe: si el valor falta o no es booleano, se **protegen** (True).
        api_keys = pol.get("api_keys", True)
        self.api_keys = api_keys if isinstance(api_keys, bool) else True
        self.secret_patterns = secret_patterns(self.api_keys)
        terms = sorted(pol.get("mask_terms") or [], key=len, reverse=True)
        self.mask_re = re.compile("|".join(re.escape(t) for t in terms), re.I) if terms else None
        enabled = set(pol.get("pii") or [])
        self.pii = [p for p in PII_PATTERNS if p[0] in enabled]

    # -- bloqueo ---------------------------------------------------------- #
    def check_blocked(self, text: str) -> None:
        low = text.lower()
        for i, term in enumerate(self.block_terms):
            if term in low:
                raise Blocked(f"contiene un termino bloqueado (block_terms[{i}])", "block_terms")
        for i, rx in enumerate(self.block_regex):
            if rx.search(text):
                raise Blocked(f"coincide con una regla de bloqueo (block_regex[{i}])", "block_regex")
        if self.secrets_action == "block":
            for name, rx in self.secret_patterns:
                if rx.search(text):
                    raise Blocked(f"se detecto un posible secreto ({name})", "secret")

    # -- segmentación ----------------------------------------------------- #
    def split(self, text: str) -> list[tuple[str, str | None, str, str]]:
        """Devuelve (kind, info, body, lang); info=None si no es un bloque con fence."""
        segs: list[tuple[str, str | None, str, str]] = []
        last = 0
        for m in FENCE_RE.finditer(text):
            if m.start() > last:
                segs.append(("prose", None, text[last : m.start()], ""))
            info, body = m.group(1), m.group(2)
            lang = (info.strip().split() or [""])[0].lower()
            if lang in PY_LANGS or (lang == "" and looks_like_python(body)):
                kind = "py"
            elif lang in TEXT_LANGS:
                kind = "text"
            else:
                kind = "other"
            segs.append((kind, info, body, lang))
            last = m.end()
        if last < len(text):
            segs.append(("prose", None, text[last:], ""))
        if len(segs) == 1 and segs[0][0] == "prose" and looks_like_python(text):
            segs = [("py", None, text, "python")]
        return segs

    def defined_names(self, body: str) -> set[str]:
        try:
            return collect_defined(body)
        except (SyntaxError, ValueError, RecursionError):
            return collect_defined_fallback(body)

    def obf(self, body: str, names: set[str], vault: Vault) -> str:
        try:
            return obfuscate_python(body, names, vault, self.code.get("strip_comments", True))
        except (tokenize.TokenError, SyntaxError):
            if self.code.get("fail_closed", True):
                raise Blocked(
                    "un bloque Python no se pudo ofuscar (sintaxis incompleta); "
                    "corrigelo o envia el bloque completo",
                    "code",
                )
            return body

    # -- sustitución de términos y PII ------------------------------------ #
    def mask(self, text: str, vault: Vault) -> str:
        if self.secrets_action == "redact":
            for name, rx in self.secret_patterns:
                def repl_secret(m, name=name):
                    vault.count("SECRET")
                    return vault.token("SECRET", m.group(0))
                text = rx.sub(repl_secret, text)
        if self.mask_re:
            def repl_term(m):
                vault.count("TERM")
                return vault.token("TERM", m.group(0), norm=m.group(0).lower())
            text = self.mask_re.sub(repl_term, text)
        for kind, rx, validator in self.pii:
            def repl(m, kind=kind, validator=validator):
                s = m.group(0)
                if validator and not validator(s):
                    return s
                vault.count(kind)
                return vault.token(kind, s)
            text = rx.sub(repl, text)
        return text

    # -- pipeline ---------------------------------------------------------- #
    def run(self, texts: list[str], vault: Vault) -> list[str]:
        for t in texts:
            self.check_blocked(t)

        all_segs = [self.split(t) for t in texts]
        obfuscate = self.code.get("obfuscate_python", True)
        names: set[str] = set()
        if obfuscate:
            for segs in all_segs:
                for kind, _info, body, _lang in segs:
                    if kind == "py":
                        names |= self.defined_names(body)

        out: list[str] = []
        for segs in all_segs:
            parts: list[str] = []
            for kind, info, body, lang in segs:
                if kind == "py" and obfuscate:
                    body = self.obf(body, names, vault)
                elif kind == "other" and self.code.get("other_languages", "block") == "block":
                    raise Blocked(
                        f"bloque de codigo '{lang}' sin soporte de ofuscacion "
                        "(cambia code.other_languages si lo aceptas)",
                        "code",
                    )
                body = self.mask(body, vault)
                parts.append(body if info is None else f"```{info}\n{body}```")
            out.append("".join(parts))
        return out
