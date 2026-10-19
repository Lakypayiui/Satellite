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
from satellite.context.lookup import lookup
from satellite.context.paths import canonical, confine, is_within
from satellite.context.project_index import ProjectIndex
from satellite.context.sensitive import is_sensitive_path

TEXT_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md", ".txt",
                   ".json", ".toml", ".yaml", ".yml", ".xml", ".sql", ".sh", ".bat",
                   ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".ini", ".cfg"}
BINARY_THRESHOLD = 1_000_000  # 1 MB: no buscar símbolos en archivos gigantes


class ContextResolver:
    """Resuelve referencias de contexto leyendo solo lo pedido."""

    def __init__(self, project_root: Path, index: ProjectIndex | None = None) -> None:
        # La raíz se canoniza una vez: todas las comprobaciones de confinamiento
        # comparan contra esta forma, así que `..` y symlinks no pueden colarla.
        root = canonical(project_root) or Path(project_root)
        self.project_root = root
        self.index = index or ProjectIndex(root)

    async def resolve_ref(
        self,
        ref: ContextRef,
        limits: ContextLimits | None = None,
        allowed_paths: tuple[Path, ...] | list[Path] = (),
    ) -> ContextSection | None:
        """Resuelve UN ref; devuelve None si no se puede resolver (se omite).

        ``allowed_paths`` son las rutas de fuera del proyecto autorizadas para
        esta petición concreta; sin ellas, cualquier ruta externa se omite.
        """
        kind = ref.kind
        if kind is ContextRefKind.PATH:
            return self._resolve_path(ref, limits, allowed_paths)
        if kind is ContextRefKind.DIR:
            return self._resolve_dir(ref, limits, allowed_paths)
        if kind is ContextRefKind.PATTERN:
            return self._resolve_pattern(ref, limits)
        if kind is ContextRefKind.SYMBOL:
            return self._resolve_symbol(ref, limits)
        if kind is ContextRefKind.SUMMARY:
            return self._resolve_summary(ref)
        if kind is ContextRefKind.PROJECT_RULES:
            return self._resolve_instructions(ref, limits)
        return None

    async def resolve_many(
        self,
        refs: list[ContextRef] | tuple[ContextRef, ...],
        limits: ContextLimits | None = None,
        allowed_paths: tuple[Path, ...] | list[Path] = (),
    ) -> ContextBundle:
        """Resuelve en orden y recorta si excede los límites (nunca fabrica)."""
        sections: list[ContextSection] = []
        for ref in refs:
            section = await self.resolve_ref(ref, limits, allowed_paths)
            if section is not None:
                sections.append(section)
        bundle = ContextBundle(tuple(sections))
        return self._trim(bundle, limits)

    # --- helpers por kind ---

    def _resolve_path(
        self,
        ref: ContextRef,
        limits: ContextLimits | None,
        allowed_paths: tuple[Path, ...] | list[Path] = (),
    ) -> ContextSection | None:
        path = self._locate_or_lookup(ref.value, allowed_paths)
        if path is None or not path.is_file():
            return None
        # Exclusión segura por defecto: un archivo sensible no entra al contexto
        # aunque se mencione (``@.env``); así no se filtra a los prompts.
        if is_sensitive_path(path):
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # Sólo se indexa lo que vive dentro del proyecto: una ruta externa autorizada se
        # lee para este turno y no debe quedar en el índice (ni en memoria ni en disco).
        if is_within(path, self.project_root):
            self.index.refresh(path)
        content = self._cap_section(content, limits)
        return ContextSection(ref=ref, content=content)

    def _resolve_dir(
        self,
        ref: ContextRef,
        limits: ContextLimits | None,
        allowed_paths: tuple[Path, ...] | list[Path] = (),
    ) -> ContextSection | None:
        path = self._locate_or_lookup(ref.value, allowed_paths)
        if path is None or not path.is_dir():
            return None
        lines: list[str] = []
        for child in sorted(path.iterdir(), key=lambda p: p.name):
            if is_sensitive_path(child):
                continue  # ni el nombre de un archivo sensible se ofrece en el listado
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
        """Todas las coincidencias del símbolo, no solo la primera.

        Antes se cortaba en la primera aparición del primer fichero, así que la búsqueda
        devolvía una sola pista. Ahora se recorre todo (los filtros de extensión y tamaño
        siguen aplicándose) y lo que se corta es el volumen: al pasarse, se dice cuántas
        coincidencias quedaron fuera en vez de fingir que eso era todo.
        """
        symbol = ref.value
        files = self.index.list_project_files()
        cap = limits.max_chars_per_section if limits and limits.max_chars_per_section else 8_000
        max_hits = 20
        hits: list[str] = []
        total = 0
        omitted = 0
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
                if symbol not in line:
                    continue
                start = max(0, i - 3)
                end = min(len(lines), i + 4)
                block = (
                    f"--- {file.relative_to(self.project_root)}:{i + 1} ---\n"
                    + "\n".join(f"{j + 1}: {lines[j]}" for j in range(start, end))
                )
                if len(hits) >= max_hits or total + len(block) > cap:
                    omitted += 1
                    continue
                hits.append(block)
                total += len(block)
        if not hits:
            return None
        content = "\n\n".join(hits)
        if omitted:
            aviso = (
                f"\n\n[{omitted} more match(es) of '{symbol}': "
                "ask for the specific file or use a pattern]"
            )
            content = content[: max(0, cap - len(aviso))] + aviso
        return ContextSection(ref=ref, content=content)

    def _resolve_instructions(self, ref: ContextRef, limits: ContextLimits | None) -> ContextSection | None:
        """Las instrucciones del proyecto (``AGENTS.md`` / ``SATELLITE.md``), si las hay."""
        from satellite.context.instructions import load_instructions

        nombre, texto = load_instructions(self.project_root)
        if not texto:
            return None
        content = self._cap_section(
            f"Project instructions ({nombre}):\n{texto}", limits
        )
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
            f"Project summary {self.project_root.name}:\n"
            + "\n".join(top_level)
            + f"\n\nTotal size (top-level): {total_size} bytes"
        )
        return ContextSection(ref=ref, content=content)

    # --- utilidades ---

    def _locate(
        self,
        value: str,
        allowed_paths: tuple[Path, ...] | list[Path] = (),
    ) -> Path | None:
        """Ruta canónica permitida para ``value``, o ``None`` si escapa del proyecto.

        Un PATH/DIR sólo se lee si su forma canónica cae dentro de la raíz o de
        una ruta autorizada para esta petición; si no, se omite (deny-by-default).
        """
        if not value or not value.strip():
            return None
        raw = Path(value).expanduser()
        candidate = raw if raw.is_absolute() else self.project_root / raw
        return confine(candidate, self.project_root, allowed_paths)

    def _locate_or_lookup(
        self,
        value: str,
        allowed_paths: tuple[Path, ...] | list[Path] = (),
    ) -> Path | None:
        """Como :meth:`_locate`, pero si la ruta exacta no existe la busca por nombre.

        La búsqueda por nombre es determinista (ruta exacta > nombre exacto > difusa) y
        confinada: si hay varias coincidencias exactas el resultado es **ambiguo** y se
        omite (nunca se lee una al azar). No relaja la frontera: el ganador pasa otra vez
        por ``confine``, así que una ruta que escape sigue sin resolverse.
        """
        located = self._locate(value, allowed_paths)
        if located is not None and located.exists():
            return located
        found = lookup(self.project_root, value).winner
        if found is None:
            return None
        return confine(self.project_root / found.relpath, self.project_root, allowed_paths)

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
