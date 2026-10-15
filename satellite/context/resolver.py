"""ContextResolver: convierte ContextRefs en secciones de contexto."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from satellite.core.context_models import (
    ContextBundle,
    ContextLimits,
    ContextRef,
    ContextRefKind,
    ContextSection,
)
from satellite.context.project_index import ProjectIndex

TEXT_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md", ".txt",
                   ".json", ".toml", ".yaml", ".yml", ".xml", ".sql", ".sh", ".bat",
                   ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".ini", ".cfg"}
BINARY_THRESHOLD = 1_000_000  # 1 MB: no buscar símbolos en archivos gigantes


class ContextResolver:
    """Resuelve referencias de contexto leyendo solo lo pedido."""

    def __init__(self, project_root: Path, index: ProjectIndex | None = None) -> None:
        self.project_root = project_root
        self.index = index or ProjectIndex(project_root)

    async def resolve_ref(
        self,
        ref: ContextRef,
        limits: ContextLimits | None = None,
    ) -> ContextSection | None:
        """Resuelve UN ref; devuelve None si no se puede resolver (se omite)."""
        kind = ref.kind
        if kind is ContextRefKind.PATH:
            return self._resolve_path(ref, limits)
        if kind is ContextRefKind.DIR:
            return self._resolve_dir(ref, limits)
        if kind is ContextRefKind.PATTERN:
            return self._resolve_pattern(ref, limits)
        if kind is ContextRefKind.SYMBOL:
            return self._resolve_symbol(ref, limits)
        if kind is ContextRefKind.SUMMARY:
            return self._resolve_summary(ref)
        return None

    async def resolve_many(
        self,
        refs: list[ContextRef] | tuple[ContextRef, ...],
        limits: ContextLimits | None = None,
    ) -> ContextBundle:
        """Resuelve en orden y recorta si excede los límites (nunca fabrica)."""
        sections: list[ContextSection] = []
        for ref in refs:
            section = await self.resolve_ref(ref, limits)
            if section is not None:
                sections.append(section)
        bundle = ContextBundle(tuple(sections))
        return self._trim(bundle, limits)

    # --- helpers por kind ---

    def _resolve_path(self, ref: ContextRef, limits: ContextLimits | None) -> ContextSection | None:
        path = self._resolve_path_absolute(ref.value)
        if path is None or not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        self.index.refresh(path)
        content = self._cap_section(content, limits)
        return ContextSection(ref=ref, content=content)

    def _resolve_dir(self, ref: ContextRef, limits: ContextLimits | None) -> ContextSection | None:
        path = self._resolve_path_absolute(ref.value)
        if path is None or not path.is_dir():
            return None
        lines: list[str] = []
        for child in sorted(path.iterdir(), key=lambda p: p.name):
            if child.is_file():
                lines.append(f"{child.name} ({child.stat().st_size} bytes)")
            elif child.is_dir():
                lines.append(f"{child.name}/")
        content = "\n".join(lines)
        content = self._cap_section(content, limits)
        return ContextSection(ref=ref, content=content)

    def _resolve_pattern(self, ref: ContextRef, limits: ContextLimits | None) -> ContextSection | None:
        pattern = ref.value
        matches = self.index.list_project_files([pattern])
        parts: list[str] = []
        total = 0
        cap = limits.max_chars_per_section if limits and limits.max_chars_per_section else 8000
        for match in matches:
            try:
                content = match.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            header = f"--- {match.relative_to(self.project_root)} ---\n"
            block = header + content + "\n"
            if total + len(block) > cap:
                remaining = cap - total
                if remaining > 0:
                    parts.append(block[:remaining])
                total = cap
                break
            parts.append(block)
            total += len(block)
        if not parts:
            return None
        return ContextSection(ref=ref, content="".join(parts))

    def _resolve_symbol(self, ref: ContextRef, limits: ContextLimits | None) -> ContextSection | None:
        symbol = ref.value
        files = self.index.list_project_files()
        hits: list[str] = []
        for file in files:
            if file.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            if file.stat().st_size > BINARY_THRESHOLD:
                continue
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                if symbol in line:
                    start = max(0, i - 3)
                    end = min(len(lines), i + 4)
                    context_lines = "\n".join(
                        f"{j + 1}: {lines[j]}" for j in range(start, end)
                    )
                    hits.append(
                        f"--- {file.relative_to(self.project_root)} ---\n{context_lines}"
                    )
                    break
            if hits:
                break
        if not hits:
            return None
        content = "\n\n".join(hits)
        content = self._cap_section(content, limits)
        return ContextSection(ref=ref, content=content)

    def _resolve_summary(self, ref: ContextRef) -> ContextSection:
        top_level: list[str] = []
        total_size = 0
        for child in sorted(self.project_root.iterdir(), key=lambda p: p.name):
            if child.name in {".git", ".venv", "node_modules", ".satellite", "__pycache__"}:
                continue
            if child.is_file():
                size = child.stat().st_size
                total_size += size
                top_level.append(f"{child.name} ({size} bytes)")
            else:
                top_level.append(f"{child.name}/")
        content = (
            f"Resumen del proyecto {self.project_root.name}:\n"
            + "\n".join(top_level)
            + f"\n\nTamaño total (top-level): {total_size} bytes"
        )
        return ContextSection(ref=ref, content=content)

    # --- utilidades ---

    def _resolve_path_absolute(self, value: str) -> Path | None:
        if not value or not value.strip():
            return None
        raw = Path(value)
        if raw.is_absolute():
            return raw
        return self.project_root / raw

    @staticmethod
    def _cap_section(content: str, limits: ContextLimits | None) -> str:
        if limits and limits.max_chars_per_section and len(content) > limits.max_chars_per_section:
            return content[: limits.max_chars_per_section]
        return content

    @staticmethod
    def _trim(bundle: ContextBundle, limits: ContextLimits | None) -> ContextBundle:
        """Recorta secciones de la última a la primera hasta cumplir límites."""
        if limits is None:
            return bundle
        if not limits.violations(bundle):
            return bundle
        sections = list(bundle.sections)
        while sections and limits.violations(ContextBundle(tuple(sections))):
            sections.pop()
        return ContextBundle(tuple(sections))
