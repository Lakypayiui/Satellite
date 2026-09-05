"""Iterative context preprocessing for the Python Satellite runtime.

Port of the C++ ``LocalPreprocessor`` (context/LocalPreprocessor.cpp): a small
LLM — which may be a local llama.cpp server OR an external provider API —
sits in front of the orchestrator. It receives the raw user goal, decides
what project context is needed (files/symbols), and the runtime fetches the
real content and re-evaluates, up to ``max_rounds``.

The model only produces structured JSON decisions; it never executes
anything (the Satellite determinism rule applies here too).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .context_schema import ContextResult, NeedInfo  # vocabulario neutro (E6)
from .llm import LLMClient, LLMConfig, load_llm_config

# Directorios ignorados al indexar/escaneo (mismo conjunto que el C++).
_IGNORED_DIRS = {
    ".git", "build", "node_modules", ".satellite", ".agent",
    "out", "dist", ".venv", "venv", "__pycache__", "CMakeFiles",
    ".idea", ".vscode", "build-make", "build2", "build_vs",
}

_SYSTEM_PROMPT = (
    "Eres un preprocesador de contexto. Analizas tareas de desarrollo y decides "
    "que contexto del proyecto hace falta. Responde SOLO con JSON: "
    '{"category": "...", "files_needed": ["ruta/relativa", ...], '
    '"symbols_needed": ["nombre_simbolo", ...], "description": "...", '
    '"sufficient": true|false}. '
    'Usa "sufficient": true cuando el contexto proporcionado ya basta '
    '(incluido cuando la tarea es de tipo "explica/resume el proyecto": '
    "con la vista general del indice alcanza). "
    'Usa category "user_input" SOLO cuando la tarea necesita datos que '
    "solo el usuario puede dar (no archivos del proyecto). "
    "Nunca inventes rutas de archivos: si un archivo no esta en la lista "
    "de archivos disponibles, no lo pidas. "
    "Nunca escribas codigo ni ejecutes nada; solo decides."
)


class ContextPreprocessor:
    """Iterative goal preprocessor backed by any configured LLM provider.

    Args:
        client: LLM client used to decide context needs. When ``None`` the
            client is built from ``config.json`` / environment.
        project_root: project root (defaults to the current directory).
        max_rounds: maximum number of decide -> fetch -> re-evaluate loops.
    """

    def __init__(
        self,
        client: LLMClient | None = None,
        project_root: str | Path = ".",
        max_rounds: int = 3,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.max_rounds = max(1, int(max_rounds))
        self.client = client or load_llm_config().create_client()
        self._index_path = self.project_root / ".satellite" / "context" / "index.json"
        self._general_view_cache: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_files(self) -> list[dict[str, Any]]:
        """Return the project index entries (path/size/symbols/mtime/...)."""
        index = self._load_index()
        return list(index.get("files", [])) if index else []

    def stale_paths(self, paths: list[str]) -> list[str]:
        """Return which of ``paths`` changed on disk since the index was built.

        Compares each file's on-disk ``st_mtime`` against the ``mtime`` stored
        in the index (the same invalidation the C++ ``changed_paths`` does,
        here triggered between plan steps instead of between builds). Files not
        present in the index are reported as stale.
        """
        index = self._load_index()
        if index is None:
            return []
        known = {str(entry.get("path", "")): entry.get("mtime") for entry in index.get("files", [])}
        stale: list[str] = []
        for relative in paths:
            full = self.project_root / relative
            try:
                disk_mtime = int(full.stat().st_mtime)  # segundos, como C++ int64
            except OSError:
                stale.append(relative)  # ya no existe: contenido obsoleto
                continue
            indexed_mtime = known.get(relative)
            if indexed_mtime is None or int(float(indexed_mtime)) != disk_mtime:
                stale.append(relative)
        return stale

    def refresh_index_entry(self, path: str) -> None:
        """Update ``index.json`` (best-effort) with the file's current mtime/size.

        Called after a plan step modified a file, so the next step does not
        re-flag it as stale. Symbols are not re-extracted (out of scope).
        """
        full = self.project_root / path
        try:
            stat = full.stat()
        except OSError:
            return
        try:
            index = self._load_index()
            if index is None:
                return
            for entry in index.get("files", []):
                if str(entry.get("path", "")) == path:
                    entry["mtime"] = int(stat.st_mtime)
                    entry["size"] = stat.st_size
                    break
            self._index_path.write_text(
                json.dumps(index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def resolve(self, paths: list[str] | None = None, symbols: list[str] | None = None) -> str:
        """Resolve concrete file paths/symbols into real project content.

        Used by the orchestrator to build the per-step context that the
        planner declared (``step.context``). Returns the ``=== path ===``
        blocks, or an empty string when nothing resolves.
        """
        needs = NeedInfo(
            category="step",
            files_needed=[str(p) for p in (paths or [])],
            symbols_needed=[str(s) for s in (symbols or [])],
            sufficient=False,
        )
        found, _missing = self._gather_from_project(needs)
        return found

    def preprocess(self, user_goal: str, user_input: str | None = None) -> ContextResult:
        """Refine ``user_goal`` by iteratively gathering project context.

        The model decides what context is missing; the runtime resolves it
        against the project index and re-evaluates. When context cannot be
        satisfied after all rounds, the user is asked for input
        (``needs_user_input=True``); if a ``user_input`` answer is supplied
        it is appended to the gathered context and the loop continues.
        """
        gathered: list[str] = []
        last = NeedInfo()
        general_given = False
        if user_input:
            gathered.append(f"Informacion del usuario:\n{user_input}")

        # Preguntas generales sobre Satellite (qué puede hacer, ayuda, etc.):
        # NO necesitan contexto del proyecto ni plan de agentes. Se responde
        # directo con una sola llamada y un contexto genérico, sin costar
        # preproceso ni compresión (las 3+ llamadas que ralentizan la respuesta).
        if _is_meta_task(user_goal) and not gathered:
            generic = (
                "Contexto general de Satellite: Satellite es un framework que "
                "orquesta microagentes. Un LLM decide el plan y microagentes "
                "especializados lo ejecutan de forma determinista y aislada. "
                "Puedes pedirle analizar un proyecto, ejecutar tareas de código, "
                "o preguntar sobre los agentes disponibles.\n"
            )
            return ContextResult(
                refined_prompt=generic + f"\nPregunta del usuario: {user_goal}\n"
            )

        # Tareas de entendimiento/lectura ("explica/resume el proyecto"):
        # el runtime inyecta la vista general directamente (determinista, sin
        # depender de que un LLM pequeño pida archivos específicos).
        if _is_explain_task(user_goal) and not gathered:
            general = self._general_project_view()
            if general:
                gathered.append(general)
                general_given = True

        for round_index in range(self.max_rounds):
            last = self._analyze_needs(user_goal, "\n".join(gathered))
            if last.category == "__error__":
                return ContextResult(needs_user_input=True, user_prompt=last.description)

            # El modelo pide información del usuario (no archivos): preguntar.
            # Si pide archivos aunque diga user_input, es contexto de proyecto
            # faltante → resolver contra el índice (no molestar al usuario).
            # Si la description NO es una pregunta (el modelo respondió o
            # analizó), tratarlo como suficiente, no como petición al usuario.
            if last.category in ("user_input", "pregunta", "user") and not (
                last.files_needed or last.symbols_needed
            ):
                description = last.description.strip()
                seems_question = (
                    description.endswith("?") or description.endswith("¿")
                    or description.lower().startswith(("que ", "qué ", "cual ", "cuál ", "como ", "cómo ", "cuando ", "cuánto ", "donde ", "dónde ", "quien ", "quién ", "what ", "which ", "how ", "when ", "where ", "please provide"))
                )
                if not seems_question:
                    # El modelo ya dio la info/analisis en la description.
                    last.sufficient = True
                    return ContextResult(
                        refined_prompt=self._build_refined_prompt(user_goal, "\n".join(gathered), last)
                    )
                return ContextResult(
                    needs_user_input=True,
                    user_prompt=self._gather_from_user(last),
                    missing_info=[last.category],
                )

            if self._has_enough_context(last):
                return ContextResult(
                    refined_prompt=self._build_refined_prompt(user_goal, "\n".join(gathered), last)
                )

            found, missing = self._gather_from_project(last)
            if found:
                gathered.append(found)
                continue

            # Nada se resolvió (p.ej. el modelo pidió archivos inexistentes).
            # Si hay índice y aún no se dio la vista general, proveerla y
            # re-evaluar — la tarea tipo "explica el proyecto" se satisface
            # con el contexto amplio, sin bloquear preguntando al usuario.
            if not general_given:
                general = self._general_project_view()
                if general:
                    gathered.append(general)
                    general_given = True
                    continue

            # Sin índice y sin poder resolver: terminar con lo que hay.
            last_round = round_index == self.max_rounds - 1
            if last_round:
                return ContextResult(
                    refined_prompt=self._build_refined_prompt(user_goal, "\n".join(gathered), last)
                )

        return ContextResult(
            refined_prompt=self._build_refined_prompt(user_goal, "\n".join(gathered), last)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _analyze_needs(self, user_goal: str, context_so_far: str) -> NeedInfo:
        """Ask the model what project context is still missing (or if done)."""
        index = self._load_index()
        files_list = ""
        if index:
            paths = [str(e.get("path", "")) for e in index.get("files", [])]
            # Lista acotada: el modelo pide rutas REALES del proyecto.
            shown = paths[:200]
            files_list = "\n".join(f"  - {p}" for p in shown)
            if len(paths) > 200:
                files_list += f"\n  ... y {len(paths) - 200} más"
        prompt = (
            f"Tarea: {user_goal}\n"
            f"Archivos del proyecto ({self._project_file_count()}):\n{files_list or '(sin indice)'}\n"
        )
        if not context_so_far:
            prompt += "Que archivos (de la lista) o simbolos necesitas para esta tarea?"
        else:
            prompt += (
                f"Contexto ya disponible:\n{context_so_far}\n"
                "Es suficiente? Si no, que archivos (de la lista) o simbolos faltan?"
            )
        try:
            text = self.client.complete(_SYSTEM_PROMPT, prompt, max_tokens=500)
        except Exception as error:  # noqa: BLE001 - surface as a user question
            error_text = str(error)
            # 401/403 de un proveedor externo: mensaje accionable sobre la key.
            if "401" in error_text or "403" in error_text or "Unauthorized" in error_text or "Authentication failed" in error_text:
                error_text = (
                    "La API key del proveedor no es válida o no está configurada "
                    f"(error de autenticación: {error_text}). Revisa la API key "
                    "y el base_url en Ajustes."
                )
            return NeedInfo(
                category="__error__",
                description=f"Error al analizar la tarea con el proveedor: {error_text}",
            )

        needs = self._parse_need_info(text)
        if not (needs.category or needs.files_needed or needs.symbols_needed or needs.description):
            # Model did not produce parseable JSON: do not block the flow.
            needs.category = "general"
            needs.description = text
            needs.sufficient = True
        if needs.sufficient:
            needs.files_needed = []
            needs.symbols_needed = []
        return needs

    def _has_enough_context(self, needs: NeedInfo) -> bool:
        return not needs.files_needed and not needs.symbols_needed

    def _gather_from_project(self, needs: NeedInfo) -> tuple[str, str]:
        """Resolve requested files/symbols against the project index.

        Returns ``(found_content, missing_summary)``. Content is the actual
        file text (capped) so the model can judge sufficiency on the next
        round. When no index exists, falls back to listing candidate paths.
        """
        index = self._load_index()
        if index is None:
            return self._fallback_scan(needs)

        resolved: list[str] = []
        missing: list[str] = []

        for wanted in needs.files_needed:
            match = self._match_path(index, wanted)
            if match:
                resolved.append(match)
            else:
                missing.append(f"archivo:{wanted}")

        for wanted in needs.symbols_needed:
            match = self._match_symbol(index, wanted)
            if match:
                resolved.append(match)
            else:
                missing.append(f"simbolo:{wanted}")

        resolved = sorted(set(resolved))
        if not resolved:
            return "", " ".join(missing)

        parts = []
        total_chars = 0
        cap = int(os.getenv("SATELLITE_CONTEXT_CHAR_CAP", "60000"))
        for path in resolved:
            full = self.project_root / path
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                missing.append(f"archivo:{path}")
                continue
            if total_chars + len(content) > cap and parts:
                break
            total_chars += len(content)
            parts.append(f"=== {path} ===\n{content}")
        if not parts:
            return "", " ".join(missing)
        header = f"Contenido del proyecto ({len(parts)} archivos, {total_chars} chars):\n"
        return header + "\n".join(parts), " ".join(missing)

    def _fallback_scan(self, needs: NeedInfo) -> tuple[str, str]:
        """No index available: scan the tree for candidate file paths."""
        missing: list[str] = []
        wanted_files = set(needs.files_needed)
        wanted_symbols = set(needs.symbols_needed)
        found_files: set[str] = set()
        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            parts = path.parts
            if any(part in _IGNORED_DIRS for part in parts):
                continue
            relative = path.relative_to(self.project_root).as_posix()
            for wanted in list(wanted_files):
                if relative == wanted or wanted in relative:
                    found_files.add(relative)
                    wanted_files.discard(wanted)
            if wanted_symbols:
                text = ""
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for symbol in list(wanted_symbols):
                    if symbol in text:
                        found_files.add(relative)
                        wanted_symbols.discard(symbol)
        missing.extend(f"archivo:{w}" for w in wanted_files)
        missing.extend(f"simbolo:{w}" for w in wanted_symbols)
        if not found_files:
            return "", " ".join(missing)
        found = sorted(found_files)
        return (
            "Archivos del proyecto (candidatos):\n" + "\n".join(f"  - {f}" for f in found),
            " ".join(missing),
        )

    def _general_project_view(self) -> str:
        """Vista general determinista del proyecto (sin LLM).

        Selecciona README, entry points y archivos representativos para tareas
        tipo "explica el proyecto" cuando el modelo pide archivos que no
        existen o no declara suficiencia. Funciona con o sin índice (escanea
        el árbol filtrando los directorios ignorados). Nunca inventa rutas.
        """
        if self._general_view_cache is not None:
            return self._general_view_cache
        view = self._build_general_view()
        self._general_view_cache = view
        return view

    def _build_general_view(self) -> str:
        index = self._load_index()
        if index:
            by_path = {str(entry.get("path", "")): entry for entry in index.get("files", [])}
        else:
            # Sin índice: escanear el árbol real podando directorios ignorados
            # (sin entrar en venv/node_modules/build/... para no recorrerlos).
            by_path = {}
            stack = [self.project_root]
            while stack:
                current = stack.pop()
                try:
                    children = list(current.iterdir())
                except OSError:
                    continue
                for child in children:
                    if child.is_dir():
                        if child.name not in _IGNORED_DIRS:
                            stack.append(child)
                    elif child.is_file():
                        relative = child.relative_to(self.project_root).as_posix()
                        try:
                            size = child.stat().st_size
                        except OSError:
                            size = 0
                        by_path[relative] = {"path": relative, "size": size}
        if not by_path:
            return ""

        selected: list[str] = []

        def _pick(candidates: list[str]) -> None:
            for candidate in candidates:
                path = next((p for p in by_path if p.lower() == candidate.lower() or p.lower().endswith("/" + candidate.lower())), None)
                if path and path not in selected:
                    selected.append(path)

        # README(s), entry points típicos y luego archivos por tamaño.
        readmes = sorted(p for p in by_path if "readme" in p.lower())
        selected.extend(readmes[:2])
        _pick(["main.py", "app.py", "cli.py", "main.cpp", "main.c", "CMakeLists.txt", "index.js", "package.json", "pyproject.toml", "requirements.txt", "setup.py"])
        # Archivos representativos: los de tamaño medio-grande (código real).
        code_exts = {".py", ".cpp", ".h", ".hpp", ".c", ".cc", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
        code_files = [
            (str(e.get("path", "")), int(e.get("size", 0)))
            for e in by_path.values()
            if str(e.get("path", "")).lower().endswith(tuple(code_exts))
        ]
        code_files.sort(key=lambda pair: (-pair[1], pair[0]))
        for path, _size in code_files:
            if len(selected) >= 12:
                break
            if path not in selected:
                selected.append(path)

        cap = int(os.getenv("SATELLITE_CONTEXT_CHAR_CAP", "60000"))
        parts: list[str] = []
        total = 0
        for path in selected:
            full = self.project_root / path
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if total + len(content) > cap and parts:
                break
            total += len(content)
            parts.append(f"=== {path} ===\n{content[:4000]}")

        # Estructura de directorios (2 niveles) para proyectos grandes: da el
        # mapa del proyecto cuando el modelo pide "ver qué archivos existen".
        structure = self._directory_structure(by_path)
        header = (
            f"Vista general del proyecto ({len(by_path)} archivos):\n"
            f"{structure}\n"
        )
        if not parts:
            return header.rstrip("\n")
        return header + "\n".join(parts)

    def _directory_structure(self, by_path: dict[str, Any]) -> str:
        """Árbol de directorios de 2 niveles (recortado a ~120 entradas)."""
        dirs: set[str] = set()
        for path in by_path:
            parts = path.split("/")
            if len(parts) > 1:
                dirs.add(parts[0])
                if len(parts) > 2:
                    dirs.add("/".join(parts[:2]))
        lines = ["Estructura de directorios:"]
        roots = sorted(d for d in dirs if "/" not in d)
        shown = 0
        for root_dir in roots:
            if shown >= 120:
                lines.append("  ...")
                break
            lines.append(f"  {root_dir}/")
            shown += 1
            children = sorted(d for d in dirs if d.startswith(root_dir + "/"))
            for child in children[:20]:
                if shown >= 120:
                    break
                lines.append(f"    {child.split('/', 1)[1]}/")
                shown += 1
        if not roots:
            lines.append("  (archivos en la raíz)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, Any] | None:
        try:
            if self._index_path.is_file():
                return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return None

    def _match_path(self, index: dict[str, Any], wanted: str) -> str | None:
        for entry in index.get("files", []):
            path = entry.get("path", "")
            if path == wanted or wanted in path:
                return path
        return None

    def _match_symbol(self, index: dict[str, Any], wanted: str) -> str | None:
        for entry in index.get("files", []):
            for symbol in entry.get("symbols", []):
                if symbol == wanted:
                    return entry.get("path", "")
        return None

    def _project_file_count(self) -> int:
        index = self._load_index()
        if index is not None:
            return len(index.get("files", []))
        count = 0
        stack = [self.project_root]
        while stack:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    if child.name not in _IGNORED_DIRS:
                        stack.append(child)
                elif child.is_file():
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Prompt / user interaction
    # ------------------------------------------------------------------

    def _gather_from_user(self, needs: NeedInfo) -> str:
        lines = [
            "Para completar la tarea se necesita informacion adicional del proyecto.",
            f"Categoria: {needs.category}",
        ]
        if needs.files_needed:
            lines.append("Archivos necesarios: " + ", ".join(needs.files_needed))
        if needs.symbols_needed:
            lines.append("Simbolos necesarios: " + ", ".join(needs.symbols_needed))
        if needs.description:
            lines.append(needs.description)
        if not needs.files_needed and not needs.symbols_needed and not needs.description:
            lines.append("Por favor proporciona una descripcion de la tarea:")
        elif not needs.description:
            lines.append("Por favor proporciona la informacion adicional:")
        return "\n".join(lines)

    def _build_refined_prompt(self, user_goal: str, context: str, needs: NeedInfo) -> str:
        parts = ["Contexto del proyecto:"]
        parts.append(context if context else "(sin contexto adicional)")
        parts.append("")
        parts.append(f"Tarea del usuario: {user_goal}")
        # NOTA: no se incluye needs.description (texto crudo del modelo):
        # puede contener JSON/planes inválidos que contaminarían al planner.
        parts.append(
            ""
            'Responde SOLO con JSON: {"steps": [{"agent_id": N, "input": {...}, '
            '"dependencies": [indices], "description": "..."}]}. '
            "Usa solo agentes del catalogo disponible. NO ejecutes tareas directamente."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_need_info(text: str) -> NeedInfo:
        """Extract the first balanced JSON object from the model output."""
        payload = _extract_first_json(text)
        if payload is None:
            return NeedInfo()
        return NeedInfo(
            category=str(payload.get("category", "")),
            files_needed=[str(f) for f in payload.get("files_needed", []) if isinstance(f, (str, int))],
            symbols_needed=[str(s) for s in payload.get("symbols_needed", []) if isinstance(s, (str, int))],
            description=str(payload.get("description", "")),
            sufficient=bool(payload.get("sufficient", False)),
        )


def _is_explain_task(user_goal: str) -> bool:
    """Detección heurística de tareas de lectura/entendimiento.

    Para estas tareas el runtime inyecta la vista general del proyecto sin
    esperar a que el LLM pida archivos (los modelos pequeños no piden rutas
    reales de forma fiable).
    """
    lowered = user_goal.lower()
    explain_words = (
        "explica", "explique", "explicar", "resume", "resumen", "resumir",
        "que hace", "qué hace", "que es", "qué es", "como funciona",
        "cómo funciona", "de que trata", "de qué trata", "en que consiste",
        "en qué consiste", "describe", "describir", "describe el proyecto",
        "cual es la estructura", "cuál es la estructura", "arquitectura",
        "resumen del proyecto", "overview", "explain", "summarize", "what is",
        "what does", "how does", "describe this", "tell me about",
    )
    return any(word in lowered for word in explain_words)


def _is_meta_task(user_goal: str) -> bool:
    """Detección de preguntas generales sobre Satellite (sin contexto de
    proyecto): qué puede hacer, ayuda, capacidades, quién eres, etc.

    Estas tareas no necesitan índice, contexto ni plan de agentes: solo una
    respuesta directa del modelo con un contexto genérico de Satellite.
    """
    lowered = user_goal.lower()
    meta_words = (
        "qué puedes hacer", "que puedes hacer", "que puedes hacer",
        "que puedes", "qué puedes", "que podrias hacer", "qué podrías hacer",
        "que podria hacer", "qué podría hacer", "en que me puedes ayudar",
        "en qué me puedes ayudar", "que me puedes ofrecer", "que eres", "qué eres",
        "quien eres", "quién eres", "que es satellite", "qué es satellite",
        "como funciona satellite", "cómo funciona satellite",
        "que hace satellite", "qué hace satellite", "que puede hacer satellite",
        "cuales son tus capacidades", "cuáles son tus capacidades",
        "que agentes tienes", "qué agentes tienes", "agentes disponibles",
        "capabilities", "capacidades", "para que sirve", "para qué sirve",
        "eres un asistente", "eres satellite", "presentate", "preséntate",
    )
    # Frases completas por substring.
    if any(word in lowered for word in meta_words):
        return True
    # "help"/"ayuda" SOLO como palabra completa (no dentro de "helper").
    import re as _re

    if _re.search(r"\bhelp\b", lowered) or _re.search(r"\bayuda\b", lowered):
        return True
    return False


def _extract_first_json(text: str) -> dict[str, Any] | None:
    """Return the first balanced JSON object in ``text`` (robust to prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif in_string and char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string and char == "{":
            depth += 1
        elif not in_string and char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                    return payload if isinstance(payload, dict) else None
                except ValueError:
                    return None
    return None
