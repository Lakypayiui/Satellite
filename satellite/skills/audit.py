"""Auditoría estática de skills: contraste de ``deterministic: true`` con el código.

Hasta ahora la marca era puramente declarativa: el manifest decía «soy
determinista» y el caché le creía. Esta auditoría mira el AST del script y de sus
módulos locales buscando lo que rompe el determinismo.

Es una **red de seguridad, no una demostración**: hay no-determinismo que no se
ve estáticamente (una llamada construida en tiempo de ejecución, un binario
externo, código generado). Un sandbox con seccomp lo probaría; esto solo sube el
listón. Por eso es **fail-closed**: si no se puede analizar algo, la skill no se
memoiza.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_DEPTH = 5

# Módulos cuyo uso rompe el determinismo. Se compara el **módulo real**, no el
# alias: `import random as r` y `from random import randint` cuentan igual.
NON_DETERMINISTIC_MODULES: tuple[str, ...] = (
    "random",
    "secrets",
    "uuid",
    "time",
    "datetime",
    "socket",
    "ssl",
    "urllib",
    "http",
    "requests",
    "httpx",
    "aiohttp",
    "subprocess",
    "multiprocessing",
)

# Ejecución de código construido en tiempo de ejecución.
DYNAMIC_MODULES: tuple[str, ...] = ("importlib",)
DYNAMIC_CALLS: tuple[str, ...] = ("eval", "exec", "compile", "__import__")

# Atributos con efecto (comparados por nombre punteado completo).
EFFECT_ATTRS: tuple[str, ...] = (
    "os.getenv",
    "os.environ",
    "os.putenv",
    "os.system",
    "os.popen",
    "os.urandom",
    "pathlib.Path.write_text",
    "pathlib.Path.write_bytes",
)

# Métodos que escriben, se llame como se llame el receptor.
WRITE_METHODS: tuple[str, ...] = ("write_text", "write_bytes")

LOCAL_SUFFIX = ".py"


@dataclass(frozen=True)
class Finding:
    """Un motivo por el que la skill no se puede memoizar con confianza."""

    # import | dynamic | effect | dependency | unreadable | syntax | language | stale_manifest
    kind: str
    detail: str
    line: int = 0
    file: str = ""

    def describe(self) -> str:
        """Texto legible para el informe y para ``reason()``."""
        where = f"{self.file}:{self.line}" if self.file and self.line else self.file
        return f"{self.detail} ({where})" if where else self.detail


def is_stdlib(module: str) -> bool:
    """True si el módulo (raíz) pertenece a la stdlib de este intérprete."""
    root = str(module or "").split(".")[0]
    return root in sys.stdlib_module_names


def audit_skill(
    skill_dir: Path | str,
    entrypoint: str = "main.py",
    *,
    language: str = "python",
    max_depth: int = MAX_DEPTH,
) -> list[Finding]:
    """Audita el entrypoint y sus módulos locales. Nunca lanza."""
    root = Path(skill_dir)
    if str(language or "").lower() != "python":
        return [
            Finding(
                kind="language",
                detail=f"lenguaje {language!r} no auditable estáticamente",
            )
        ]
    script = root / str(entrypoint or "")
    return _audit_file(script, root, depth=0, max_depth=max_depth, seen=set())


def code_digest(
    skill_dir: Path | str,
    entrypoint: str = "main.py",
    *,
    max_depth: int = MAX_DEPTH,
) -> str:
    """sha256 del entrypoint y de sus módulos locales (``""`` si no se puede leer).

    El formato de un solo fichero coincide con el que usaba el registro
    (``sha256(script_bytes)``) para no invalidar manifests existentes. Con
    módulos locales se hashea una lista ordenada ``ruta\\0sha256``.
    """
    root = Path(skill_dir)
    script = root / str(entrypoint or "")
    files = _collect_code_files(
        script, root, depth=0, max_depth=max_depth, seen=set()
    )
    if not files:
        return ""
    if len(files) == 1 and files[0] == script:
        try:
            return _sha256_bytes(script.read_bytes())
        except OSError:
            return ""
    parts: list[str] = []
    for path in sorted(files, key=lambda item: _relative_name(item, root)):
        try:
            content = path.read_bytes()
        except OSError:
            return ""
        parts.append(f"{_relative_name(path, root)}\0{_sha256_bytes(content)}")
    return _sha256_bytes("\n".join(parts).encode("utf-8"))


def reason_for(findings: list[Finding]) -> str:
    """Motivo compacto («auditoría: import random (línea 3)») o ``""``."""
    if not findings:
        return ""
    first = findings[0].describe()
    extra = f" (+{len(findings) - 1} más)" if len(findings) > 1 else ""
    return f"auditoría: {first}{extra}"


# --- interno ---------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    """sha256 hexadecimal de un bloque de bytes."""
    return hashlib.sha256(data).hexdigest()


def _relative_name(path: Path, root: Path) -> str:
    """Ruta relativa POSIX (o el nombre si el fichero quedó fuera de la raíz)."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _resolve_local(module: str, current: Path, skill_dir: Path) -> Path | None:
    """Primer fichero local que corresponde a un import, o ``None``."""
    candidates = [
        current.parent / f"{module}{LOCAL_SUFFIX}",
        current.parent / module / f"__init__{LOCAL_SUFFIX}",
        skill_dir / f"{module}{LOCAL_SUFFIX}",
        skill_dir / module / f"__init__{LOCAL_SUFFIX}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _collect_code_files(
    path: Path,
    skill_dir: Path,
    *,
    depth: int,
    max_depth: int,
    seen: set,
) -> list[Path]:
    """Ficheros de código alcanzables desde el entrypoint (mismo recorrido que la auditoría)."""
    resolved = path.resolve() if path.exists() else path
    if resolved in seen:
        return []
    seen.add(resolved)
    if depth > max_depth:
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return [path] if path.is_file() else []
    files = [path] if path.is_file() else []
    for module, _line in _local_imports(tree):
        local = _resolve_local(module, path, skill_dir)
        if local is not None:
            files.extend(
                _collect_code_files(
                    local,
                    skill_dir,
                    depth=depth + 1,
                    max_depth=max_depth,
                    seen=seen,
                )
            )
    return files


def _audit_file(
    path: Path, skill_dir: Path, *, depth: int, max_depth: int, seen: set
) -> list[Finding]:
    """Un fichero y, recursivamente, sus imports locales."""
    resolved = path.resolve() if path.exists() else path
    if resolved in seen:
        return []
    seen.add(resolved)
    if depth > max_depth:
        return [
            Finding(
                kind="unreadable",
                detail=f"profundidad de auditoría agotada ({max_depth})",
                file=str(path),
            )
        ]
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [
            Finding(
                kind="unreadable",
                detail="no se pudo leer el script",
                file=str(path),
            )
        ]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [
            Finding(
                kind="syntax",
                detail=f"sintaxis inválida: {exc.msg}",
                line=int(exc.lineno or 0),
                file=str(path),
            )
        ]

    name = path.name
    findings = _audit_tree(tree, name)
    for module, line in _local_imports(tree):
        findings.extend(
            _audit_local(module, line, path, skill_dir, depth=depth, max_depth=max_depth, seen=seen, file=name)
        )
    return findings


def _audit_local(
    module: str,
    line: int,
    current: Path,
    skill_dir: Path,
    *,
    depth: int,
    max_depth: int,
    seen: set,
    file: str,
) -> list[Finding]:
    """Resuelve un import local y lo audita, o lo declara no auditable."""
    local = _resolve_local(module, current, skill_dir)
    if local is not None:
        return _audit_file(
            local, skill_dir, depth=depth + 1, max_depth=max_depth, seen=seen
        )
    return [
        Finding(
            kind="dependency",
            detail=f"dependencia no auditable: {module}",
            line=line,
            file=file,
        )
    ]


def _local_imports(tree: ast.AST) -> list[tuple[str, int]]:
    """Imports que hay que resolver localmente o marcar como no auditables."""
    pending: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if not is_stdlib(root):
                    pending.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # import relativo: siempre local
                pending.append((node.module or "", node.lineno))
            elif node.module and not is_stdlib(node.module.split(".")[0]):
                pending.append((node.module, node.lineno))
    return pending


def _audit_tree(tree: ast.AST, file: str) -> list[Finding]:
    """Hallazgos del propio árbol (imports no deterministas, efectos, dinámico)."""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in NON_DETERMINISTIC_MODULES:
                    findings.append(
                        Finding("import", f"import {root}", node.lineno, file)
                    )
                elif root in DYNAMIC_MODULES:
                    findings.append(
                        Finding("dynamic", f"import {root}", node.lineno, file)
                    )
        elif isinstance(node, ast.ImportFrom):
            root = str(node.module or "").split(".")[0]
            if root in NON_DETERMINISTIC_MODULES:
                findings.append(Finding("import", f"import {root}", node.lineno, file))
            elif root in DYNAMIC_MODULES:
                findings.append(Finding("dynamic", f"import {root}", node.lineno, file))
        elif isinstance(node, ast.Call):
            findings.extend(_call_findings(node, file))
        elif isinstance(node, ast.Attribute):
            # atributos con efecto que no se llaman (p. ej. `os.environ`)
            dotted = _dotted_name(node)
            if dotted in EFFECT_ATTRS:
                findings.append(Finding("effect", dotted, node.lineno, file))
    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Quita repetidos (una llamada a ``os.getenv`` se ve por dos caminos)."""
    seen: set[tuple[str, str, int]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.kind, finding.detail, finding.line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _call_findings(node: ast.Call, file: str) -> list[Finding]:
    """Hallazgos de una llamada: dinámicas, atributos con efecto y escrituras."""
    findings: list[Finding] = []
    func = node.func
    if isinstance(func, ast.Name) and func.id in DYNAMIC_CALLS:
        findings.append(Finding("dynamic", f"{func.id}()", node.lineno, file))
        return findings
    if isinstance(func, ast.Attribute):
        dotted = _dotted_name(func)
        if dotted in EFFECT_ATTRS or func.attr in WRITE_METHODS:
            findings.append(Finding("effect", f"{dotted}()", node.lineno, file))
            return findings
    if isinstance(func, ast.Name) and func.id == "open":
        mode = _literal_mode(node)
        if mode and any(flag in mode for flag in "wax+"):
            findings.append(
                Finding("effect", f"open(..., {mode!r})", node.lineno, file)
            )
    return findings


def _dotted_name(node: ast.Attribute) -> str:
    """Nombre punteado de un atributo (``os.path.join`` → ``os.path.join``)."""
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        if parts[0] == "Path":
            parts[-1] = "pathlib.Path"
    return ".".join(reversed(parts))


def _literal_mode(node: ast.Call) -> str:
    """Modo literal de ``open(...)`` (``""`` si no es un literal)."""
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        value = node.args[1].value
        return str(value) if isinstance(value, str) else ""
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return str(value) if isinstance(value, str) else ""
    return ""
