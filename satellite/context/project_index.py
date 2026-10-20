"""Índice ligero de proyecto: solo lo que ya se resolvió/exploró."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from satellite.context.sensitive import is_index_excluded

IGNORED_DIRS = {
    ".git", ".venv", "venv", "virtualenv", "myenv", "site-packages",
    "node_modules", ".satellite", ".agent",
    "__pycache__", "build", "dist", "out",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}
_MAX_INDEX_FILE_BYTES = 1_000_000
_MAX_CONTEXT_CHARS = 12_000
# Extensiones que casi siempre son binarias: no se intentan leer como texto. Es un
# atajo, no un filtro de contenido: lo que decide de verdad es la sonda de bytes.
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".icns", ".webp", ".tiff",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".o", ".a", ".obj",
    ".class", ".pyc", ".pyo", ".pyd", ".wasm", ".woff", ".woff2", ".ttf",
    ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".ogg",
    ".flac", ".webm", ".db", ".sqlite", ".sqlite3",
}
# Separador de los bloques de contenido dentro de la vista de proyecto (``--- ruta ---``).
# Se usa para saber si la vista incluye texto de archivos o sólo el inventario.
_CONTENT_BLOCK = "\n--- "

# Patrones por lenguaje para detectar funciones, clases e imports
_LANG_PATTERNS: dict[str, dict[str, re.Pattern]] = {
    ".py": {
        "def": re.compile(r"^\s*def\s+([a-zA-Z_][\w]*)"),
        "class": re.compile(r"^\s*class\s+([a-zA-Z_][\w]*)"),
        "import": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"),
    },
    ".ts": {
        "def": re.compile(r"^\s*(?:export\s+)?(?:function|const\s+\w+\s*=\s*(?:async\s*)?\()?\s*([a-zA-Z_$][\w$]*)"),
        "class": re.compile(r"^\s*(?:export\s+)?class\s+([a-zA-Z_$][\w$]*)"),
        "import": re.compile(r"^\s*import\s+.*from\s+['\"]([^'\"]+)['\"]|^\s*import\s+['\"]([^'\"]+)['\"]"),
    },
    ".tsx": {
        "def": re.compile(r"^\s*(?:export\s+)?(?:function|const\s+\w+\s*=\s*)\s*([a-zA-Z_$][\w$]*)"),
        "class": re.compile(r"^\s*(?:export\s+)?class\s+([a-zA-Z_$][\w$]*)"),
        "import": re.compile(r"^\s*import\s+.*from\s+['\"]([^'\"]+)['\"]|^\s*import\s+['\"]([^'\"]+)['\"]"),
    },
    ".js": {
        "def": re.compile(r"^\s*(?:function|const\s+\w+\s*=\s*(?:async\s*)?)\s*([a-zA-Z_$][\w$]*)"),
        "class": re.compile(r"^\s*class\s+([a-zA-Z_$][\w$]*)"),
        "import": re.compile(r"^\s*import\s+.*from\s+['\"]([^'\"]+)['\"]|^\s*require\(['\"]([^'\"]+)['\"]"),
    },
    ".jsx": {
        "def": re.compile(r"^\s*(?:function|const\s+\w+\s*=\s*)\s*([a-zA-Z_$][\w$]*)"),
        "class": re.compile(r"^\s*class\s+([a-zA-Z_$][\w$]*)"),
        "import": re.compile(r"^\s*import\s+.*from\s+['\"]([^'\"]+)['\"]"),
    },
    ".c": {"def": re.compile(r"^\s*[a-zA-Z_]\w*\s+([a-zA-Z_]\w*)\s*\("), "class": re.compile(r"(?:^|,)"), "import": re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)")},
    ".cpp": {"def": re.compile(r"^\s*[a-zA-Z_:<>~]\w*\s+([a-zA-Z_]\w*)\s*\("), "class": re.compile(r"^\s*class\s+([a-zA-Z_]\w*)"), "import": re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)")},
    ".h": {"def": re.compile(r"^\s*[a-zA-Z_:<>~]\w*\s+([a-zA-Z_]\w*)\s*\("), "class": re.compile(r"^\s*class\s+([a-zA-Z_]\w*)"), "import": re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)")},
    ".hpp": {"def": re.compile(r"^\s*[a-zA-Z_:<>~]\w*\s+([a-zA-Z_]\w*)\s*\("), "class": re.compile(r"^\s*class\s+([a-zA-Z_]\w*)"), "import": re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)")},
    ".go": {"def": re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?([A-Za-z_]\w*)"), "class": re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(struct|interface)"), "import": re.compile(r"^\s*[\"]([^\"]+)[\"]")},
    ".rs": {"def": re.compile(r"^\s*(?:pub\s+)?fn\s+([a-z_]\w*)"), "class": re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)"), "import": re.compile(r"^\s*use\s+([\w:]+)")},
    ".java": {"def": re.compile(r"^\s*(?:public|private|protected)\s+[\w<>\[\]]+\s+([a-z]\w*)\s*\("), "class": re.compile(r"^\s*(?:public\s+)?(?:final\s+)?class\s+([A-Z]\w*)"), "import": re.compile(r"^\s*import\s+([\w.]+);")},
}

_SKIP_SYMBOLS = {"", "if", "for", "while", "return", "switch", "catch", "new"}

# Preguntas por el proyecto (overview/README): piden una explicación, no una acción.
_EXPLAIN_RE = re.compile(
    r"\b(qué|que|trata|describe|explica|explique|resumen|resum|hace|overview|about|"
    r"arquitectura|funciona|consiste|diagramas?|diagrams?|flowcharts?|flow[ -]?charts?)\b",
    re.IGNORECASE,
)


def explain_goal(goal: str) -> bool:
    """True si el goal pregunta por el proyecto (overview/README) y no por una acción."""
    return bool(_EXPLAIN_RE.search(str(goal or "")))


def has_readable_content(context: str) -> bool:
    """True si la vista de proyecto incluye el texto de algún archivo legible.

    La vista siempre trae el inventario (el árbol de rutas); esto distingue si además
    se pudo leer contenido, para no confundir «hay archivos» con «se pudo leer algo».
    """
    return _CONTENT_BLOCK in str(context or "")


class ProjectIndex:
    """Índice incremental ligero por mtime + análisis estructural bajo demanda."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self._stats: dict[str, dict] = {}  # relpath -> {size, mtime_ns}
        self._structure: dict[str, dict] = {}  # relpath -> {functions, classes, imports}
        self._dir_cache: dict[Path, list[Path]] = {}  # dir -> archivos listados
        self._ensured = False  # el índice persistido ya se cargó/actualizó al menos una vez
        self.index_path = self.project_root / ".satellite" / "context" / "index.json"

    def ensure_persisted(self) -> None:
        """Carga el índice estático guardado y lo actualiza si cambió el proyecto.

        El JSON guarda únicamente rutas y metadatos estructurales; nunca contenido.
        """
        saved: dict[str, dict] = {}
        payload: dict = {}
        valid_snapshot = False
        try:
            loaded = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
            if payload.get("schema") == 1 and payload.get("root") == str(self.project_root):
                valid_snapshot = True
                saved = {
                    str(item["path"]): item
                    for item in payload.get("files", [])
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                }
        except (OSError, ValueError, TypeError):
            pass

        paths = self.list_project_files()
        current: dict[str, dict] = {}
        for path in paths:
            rel = self._relpath(path)
            stat = self.stat(path)
            if stat is None:
                continue
            current[rel] = stat
            previous = saved.get(rel, {})
            if previous.get("mtime_ns") == stat["mtime_ns"] and previous.get("size") == stat["size"]:
                self._stats[rel] = stat
                self._structure[rel] = {
                    key: previous.get(key, []) for key in ("functions", "classes", "imports")
                }
            else:
                self.refresh(path)
                self.structure(path)

        # A partir de aquí el índice en memoria cubre TODO el proyecto, aunque la
        # escritura a disco falle (p. ej. proyecto de solo lectura).
        self._ensured = True

        # Un archivo borrado deja de estar en ``current``: si no se retira de la
        # memoria, seguiría apareciendo en el inventario de turnos siguientes (el
        # contexto de un índice reutilizado). El índice refleja el proyecto actual.
        for rel in set(self._stats) - set(current):
            self._stats.pop(rel, None)
            self._structure.pop(rel, None)

        records = []
        for rel in sorted(current):
            stat = current[rel]
            structure = self._structure.get(rel, {"functions": [], "classes": [], "imports": []})
            records.append({"path": rel, **stat, **structure})
        if valid_snapshot and payload.get("files", []) == records:
            return
        payload = {
            "schema": 1,
            "root": str(self.project_root),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "files": records,
        }
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="index-", suffix=".tmp", dir=self.index_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, self.index_path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        except OSError:
            # El índice en memoria sigue siendo útil en proyectos de solo lectura.
            return

    def context_for_goal(self, goal: str, *, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
        """Crea una vista compacta del proyecto y añade archivos pertinentes al goal."""
        if not self._ensured:
            self.ensure_persisted()
        rels = sorted(self._stats)
        if not rels:
            return ""

        # Path-like tokens (`test.py`, `src/main.ts`) are explicit references: the file
        # the user names must be preferred over generic "main.*" or README heuristics.
        words = {
            word.lower().strip(".,:;!?")
            for word in re.findall(r"[\w./\\-]{3,}", goal)
        }
        explain = explain_goal(goal)
        ranked: list[tuple[int, str, Path]] = []
        for rel in rels:
            path = self.project_root / rel
            name = Path(rel).name.lower()
            rel_lower = rel.lower()
            score = sum(1 for word in words if word in rel_lower) * 3
            for word in words:
                normalized = word.replace("\\", "/")
                if not ("." in normalized or "/" in normalized):
                    continue
                if (
                    normalized == name
                    or normalized == rel_lower
                    or rel_lower.endswith("/" + normalized)
                ):
                    score += 300
                    break
            if name in {"readme.md", "readme.rst", "readme.txt"}:
                score += 100 if explain else 12
            if name in {"pyproject.toml", "package.json", "cargo.toml", "go.mod", "requirements.txt"}:
                score += 15 if explain else 2
            if explain and ("main." in name or name in {"app.py", "__main__.py", "index.ts", "index.js"}):
                score += 8
            ranked.append((score, rel, path))
        if explain and not any(Path(rel).name.lower().startswith("readme") for rel in rels):
            code_exts = set(_LANG_PATTERNS)
            representatives = [item for item in ranked if item[2].suffix.lower() in code_exts]
            representatives.sort(key=lambda item: (-self._stats.get(item[1], {}).get("size", 0), item[1]))
            selected_reps = {item[1] for item in representatives[:5]}
            ranked = [(score + (5 if rel in selected_reps else 0), rel, path) for score, rel, path in ranked]
        ranked.sort(key=lambda item: (-item[0], item[1]))

        lines = ["Índice estructural del proyecto (árbol hasta dos niveles):"]
        tree = [rel for rel in rels if rel.count("/") <= 1]
        lines.extend(f"- {rel}" for rel in tree[:100])
        remaining = max(0, max_chars - sum(len(line) + 1 for line in lines))
        selected = [item for item in ranked if item[0] > 0][:6]
        for _, rel, path in selected:
            if not path.is_file():
                continue
            content = self._readable_text(path)
            if content is None:
                continue
            block = f"{_CONTENT_BLOCK}{rel} ---\n{content}"
            if len(block) > remaining:
                block = block[:remaining]
            if block:
                lines.append(block)
                remaining -= len(block)
            if remaining <= 0:
                break
        return "\n".join(lines)[:max_chars]

    def _readable_text(self, path: Path, *, limit: int = 4000) -> str | None:
        """Texto legible de un archivo, o ``None`` si no es seguro/legible.

        Decide por **contenido** (sonda de bytes), no por una lista cerrada de
        extensiones: así archivos relevantes con extensiones distintas (``.vue``,
        ``.lua``, ``Dockerfile``, ``LICENSE``, etc.) también entran al contexto. Se
        respetan las exclusiones sensibles y el límite de tamaño, y se descarta lo que
        parece binario (un byte NUL) sin gastar memoria de más.
        """
        if path.suffix.lower() in _BINARY_SUFFIXES:
            return None
        if not self._safe_index_file(path):
            return None
        try:
            with open(path, "rb") as stream:
                data = stream.read(limit * 4)
        except OSError:
            return None
        if b"\x00" in data:
            return None
        return data.decode("utf-8", errors="replace")[:limit]

    def _safe_index_file(self, path: Path) -> bool:
        if is_index_excluded(path):
            return False
        try:
            path.resolve().relative_to(self.project_root)
            return path.stat().st_size <= _MAX_INDEX_FILE_BYTES
        except (OSError, ValueError):
            return False

    def stat(self, path: Path) -> dict | None:
        """Devuelve {size, mtime_ns} del path, o None si no existe."""
        try:
            st = path.stat()
        except OSError:
            return None
        return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}

    def needs_refresh(self, path: Path) -> bool:
        """True si el mtime actual difiere del guardado en el índice."""
        rel = self._relpath(path)
        current = self.stat(path)
        if current is None:
            return True
        cached = self._stats.get(rel)
        if cached is None:
            return True
        return cached["mtime_ns"] != current["mtime_ns"]

    def refresh(self, path: Path) -> None:
        """Actualiza la entrada del índice (stats + estructura) para el path."""
        rel = self._relpath(path)
        current = self.stat(path)
        if current is not None:
            self._stats[rel] = current
        else:
            self._stats.pop(rel, None)
            self._structure.pop(rel, None)

    def structure(self, path: Path) -> dict:
        """Análisis estructural: {functions, classes, imports} de un archivo."""
        rel = self._relpath(path)
        if rel in self._structure:
            return self._structure[rel]
        result = {"functions": [], "classes": [], "imports": []}
        patterns = _LANG_PATTERNS.get(path.suffix)
        if patterns:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            # mapeo patrón -> clave del resultado
            key_map = {"def": "functions", "class": "classes", "import": "imports"}
            for i, line in enumerate(text.splitlines(), start=1):
                line_s = line.strip()
                for key, pat in patterns.items():
                    m = pat.search(line_s)
                    if m:
                        for g in m.groups():
                            if g and g not in _SKIP_SYMBOLS:
                                result[key_map.get(key, key)].append({"name": g, "line": i})
                                break
                        break  # una categoría por línea
        self._structure[rel] = result
        return result

    def find_symbol(self, symbol: str) -> list[dict]:
        """Busca un símbolo (función/clase) en todo el proyecto (estructura).

        Recorre los archivos de código y usa el análisis estructural; devuelve
        [{path, kind, line}] para cada coincidencia.
        """
        hits: list[dict] = []
        for path in self.list_project_files():
            if path.suffix.lower() not in _LANG_PATTERNS:
                continue
            structure = self.structure(path)
            for key in ("functions", "classes"):
                for item in structure.get(key, []):
                    if item.get("name") == symbol:
                        hits.append({"path": self._relpath(path), "kind": key, "line": item.get("line")})
        return hits

    def all_imports(self) -> dict[str, list[str]]:
        """Todos los imports del proyecto: {relpath: [imports]}."""
        result: dict[str, list[str]] = {}
        for path in self.list_project_files():
            if path.suffix.lower() not in _LANG_PATTERNS:
                continue
            imports = self.structure(path).get("imports", [])
            names = [i["name"] for i in imports]
            if names:
                result[self._relpath(path)] = names
        return result

    def list_project_files(self, patterns: list[str] | None = None) -> list[Path]:
        """Lista archivos del proyecto (con glob simple), ignorando dirs típicos.

        Se recorre con ``os.walk`` y no con ``Path.walk``: el segundo existe desde
        Python 3.12 y aquí se soporta 3.11, donde no es sólo que falte, sino un
        ``AttributeError`` en tiempo de ejecución.
        """
        files: list[Path] = []
        for raw_root, dirs, names in os.walk(self.project_root):
            root = Path(raw_root)
            dirs[:] = [
                d
                for d in dirs
                if d not in IGNORED_DIRS
                # Generic virtualenv detection: a directory with `pyvenv.cfg` is a
                # Python environment, whatever the user named it (`myenv`, `env`...).
                and not (root / d / "pyvenv.cfg").exists()
            ]
            for name in names:
                candidate = root / name
                if not self._safe_index_file(candidate):
                    continue
                rel = (root / name).relative_to(self.project_root)
                rel_str = rel.as_posix()
                if patterns and not any(fnmatch.fnmatch(rel_str, p) for p in patterns):
                    continue
                files.append(root / name)
        files.sort()
        return files

    def _relpath(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return str(path)
