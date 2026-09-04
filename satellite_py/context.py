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

_SYSTEM_PROMPT = (
    "Eres un preprocesador de contexto. Analizas tareas de desarrollo y decides "
    "que contexto del proyecto hace falta. Responde SOLO con JSON: "
    '{"category": "...", "files_needed": ["ruta/relativa", ...], '
    '"symbols_needed": ["nombre_simbolo", ...], "description": "...", '
    '"sufficient": true|false}. '
    'Usa "sufficient": true cuando el contexto proporcionado ya basta. '
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
        if user_input:
            gathered.append(f"Informacion del usuario:\n{user_input}")

        for round_index in range(self.max_rounds):
            last = self._analyze_needs(user_goal, "\n".join(gathered))
            if last.category == "__error__":
                return ContextResult(needs_user_input=True, user_prompt=last.description)

            if self._has_enough_context(last):
                return ContextResult(
                    refined_prompt=self._build_refined_prompt(user_goal, "\n".join(gathered), last)
                )

            found, missing = self._gather_from_project(last)
            if found:
                gathered.append(found)
                continue

            # Nothing could be resolved this round.
            last_round = round_index == self.max_rounds - 1
            if last_round:
                return ContextResult(
                    needs_user_input=True,
                    user_prompt=self._gather_from_user(last),
                    missing_info=[missing or last.category],
                )

        return ContextResult(
            refined_prompt=self._build_refined_prompt(user_goal, "\n".join(gathered), last)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _analyze_needs(self, user_goal: str, context_so_far: str) -> NeedInfo:
        """Ask the model what project context is still missing (or if done)."""
        prompt = (
            f"Tarea: {user_goal}\n"
            f"Archivos del proyecto: {self._project_file_count()}\n"
        )
        if not context_so_far:
            prompt += "Que informacion del proyecto necesitas para esta tarea?"
        else:
            prompt += (
                f"Contexto ya disponible:\n{context_so_far}\n"
                "Es suficiente? Si no, que archivos o simbolos faltan?"
            )
        try:
            text = self.client.complete(_SYSTEM_PROMPT, prompt, max_tokens=500)
        except Exception as error:  # noqa: BLE001 - surface as a user question
            return NeedInfo(
                category="__error__",
                description=f"Error al analizar la tarea con el modelo local: {error}",
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
            if any(part in {".git", ".satellite", "build", "build-make", "build2", "build_vs", ".venv"} for part in parts):
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
        return sum(1 for path in self.project_root.rglob("*") if path.is_file())

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
        if not needs.files_needed and not needs.symbols_needed:
            lines.append("Por favor proporciona una descripcion de la tarea:")
        else:
            lines.append("Por favor proporciona la informacion adicional:")
        return "\n".join(lines)

    def _build_refined_prompt(self, user_goal: str, context: str, needs: NeedInfo) -> str:
        parts = ["Contexto del proyecto:"]
        parts.append(context if context else "(sin contexto adicional)")
        parts.append("")
        parts.append(f"Tarea del usuario: {user_goal}")
        if needs.description:
            parts.append(f"Analisis del modelo: {needs.description}")
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
