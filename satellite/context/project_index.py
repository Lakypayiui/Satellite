"""Índice ligero de proyecto: solo lo que ya se resolvió/exploró."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

IGNORED_DIRS = {".git", ".venv", "node_modules", ".satellite", "__pycache__", "build", "dist"}

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


class ProjectIndex:
    """Índice incremental ligero por mtime + análisis estructural bajo demanda."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._stats: dict[str, dict] = {}  # relpath -> {size, mtime_ns}
        self._structure: dict[str, dict] = {}  # relpath -> {functions, classes, imports}
        self._dir_cache: dict[Path, list[Path]] = {}  # dir -> archivos listados

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
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in names:
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
