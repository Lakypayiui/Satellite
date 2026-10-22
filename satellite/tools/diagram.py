"""Render Mermaid diagrams to SVG with Pele, as a conditional capability.

**What Pele is.** `Pele <https://github.com/obsidianmd/pele>`_ renders Mermaid
diagrams to SVG. It is a JavaScript *library* (npm ``pele``, MIT, no runtime
dependencies, Node >= 20), not a CLI: the official supported method is
``npm install pele`` followed by ``import { render } from 'pele'``.

**Integration strategy.** Satellite never installs or vendors third-party code on
its own. We ship a tiny Node helper (``pele_render.mjs``) that dynamically imports
the *user's* installed ``pele`` and calls the documented ``render()`` API. The
helper is pure glue: no diagram logic lives here. Node and the ``pele`` package are
resolved at call time; when either is missing the tool reports
``pele_unavailable`` and the caller falls back to the Mermaid source, so the normal
flow is preserved.

**Setup (only needed to actually render SVGs).**

1. Install Node.js >= 20.
2. ``npm install pele`` (locally in the project, or ``npm install -g pele``).
3. Point Satellite at it when it is not auto-discovered:
   ``SATELLITE_PELE_MODULE`` = absolute path to ``pele/dist/pele.js``, or
   ``SATELLITE_PELE_PATH`` = a directory containing ``node_modules/pele`` (or the
   ``node_modules`` directory itself).

Discovery order: ``SATELLITE_PELE_MODULE`` → ``SATELLITE_PELE_PATH`` → the project's
``node_modules`` → the global npm root → ``NODE_PATH`` entries.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from satellite.core.tool_models import ToolResult
from satellite.tools.base import Tool, ToolContext
from satellite.tools.fs import _read_previous, _record_change, _resolve

HELPER_PATH = Path(__file__).with_name("pele_render.mjs")
DEFAULT_TIMEOUT_S = 30
DEFAULT_DIAGRAM_DIR = "diagrams"

# Setup guidance reused across the tool's error and the flow's user-facing fallback.
SETUP_INSTRUCTIONS = (
    "Pele no está disponible: instala Node.js >= 20 y el paquete 'pele' "
    "(npm install pele), o define SATELLITE_PELE_MODULE con la ruta a "
    "pele/dist/pele.js (o SATELLITE_PELE_PATH con el directorio que contiene "
    "node_modules/pele)."
)


@dataclass
class RenderOutcome:
    """Resultado de intentar renderizar Mermaid con Pele."""

    ok: bool
    svg: str = ""
    diagram_type: str = ""
    width: int = 0
    height: int = 0
    error: str = ""
    error_code: str = ""


@dataclass
class PeleStatus:
    """Diagnóstico de disponibilidad (para mensajes y tests)."""

    node: str | None = None
    module: Path | None = None
    details: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        """True si hay Node **y** un módulo pele resoluble."""
        return bool(self.node and self.module)


def _candidate_node_paths() -> list[str]:
    """Rutas habituales de ``node`` cuando el PATH no lo incluye.

    Una app de macOS lanzada desde Finder hereda un PATH mínimo
    (``/usr/bin:/bin:/usr/sbin:/sbin``), así que ``shutil.which`` no ve una
    instalación de nvm, Homebrew, Volta o asdf aunque exista. Se revisan esas
    ubicaciones también en Linux y Windows (nvm-windows, Node por defecto).
    """
    candidates: list[str] = []
    explicit = os.environ.get("SATELLITE_NODE")
    if explicit:
        candidates.append(explicit)
    found = shutil.which("node")
    if found:
        candidates.append(found)
    home = Path.home()
    if sys.platform.startswith("win"):
        for base in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if base:
                candidates.append(str(Path(base) / "nodejs" / "node.exe"))
                candidates.append(str(Path(base) / "Programs" / "nodejs" / "node.exe"))
        candidates.append(str(home / "scoop" / "apps" / "nodejs" / "current" / "node.exe"))
    else:
        candidates.extend(
            ["/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"]
        )
        candidates.extend(
            sorted(
                glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "bin" / "node")),
                reverse=True,
            )
        )
        candidates.extend(
            [
                str(home / ".volta" / "bin" / "node"),
                str(home / ".asdf" / "shims" / "node"),
                str(home / ".local" / "bin" / "node"),
                str(home / "n" / "bin" / "node"),
            ]
        )
    return candidates


def find_node() -> str | None:
    """Ruta del ejecutable ``node`` (PATH y ubicaciones habituales), o ``None``."""
    for candidate in _candidate_node_paths():
        try:
            path = Path(candidate).expanduser()
        except (OSError, ValueError):
            continue
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _npm_binary(node: str | None = None) -> str | None:
    """Ejecutable ``npm``: el del PATH o el que acompaña al ``node`` hallado."""
    found = shutil.which("npm")
    if found:
        return found
    if node:
        name = "npm.cmd" if sys.platform.startswith("win") else "npm"
        candidate = Path(node).parent / name
        if candidate.is_file():
            return str(candidate)
    return None


def _npm_global_root(node: str | None = None) -> Path | None:
    """Raíz global de npm (``npm root -g``), o ``None`` si npm no está disponible."""
    npm = _npm_binary(node)
    if npm is None:
        return None
    env = dict(os.environ)
    if node:
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [npm, "root", "-g"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    candidate = Path(proc.stdout.strip())
    return candidate if candidate.is_dir() else None


def _global_module_roots(node: str | None) -> list[Path]:
    """Directorios ``node_modules`` globales probables (sin depender del PATH)."""
    roots: list[Path] = []
    resolved = _npm_global_root(node)
    if resolved is not None:
        roots.append(resolved)
    if node:
        node_path = Path(node)
        if sys.platform.startswith("win"):
            roots.append(node_path.parent / "node_modules")
        else:
            # <nvm>/versions/node/<v>/bin/node -> <nvm>/.../<v>/lib/node_modules
            roots.append(node_path.parent.parent / "lib" / "node_modules")
    home = Path.home()
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "npm" / "node_modules")
    else:
        roots.extend(
            [
                Path("/opt/homebrew/lib/node_modules"),
                Path("/usr/local/lib/node_modules"),
                home / ".npm-global" / "lib" / "node_modules",
                home / ".npm" / "lib" / "node_modules",
            ]
        )
        for entry in glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "lib" / "node_modules")):
            roots.append(Path(entry))
    return roots


def _self_bundled_module() -> Path | None:
    """``pele`` instalado junto a la app de escritorio de este mismo repo.

    En desarrollo, ``npm install`` dentro de ``desktop/`` deja ``pele`` ahí aunque
    el proyecto abierto sea otro. El paquete Python vive en
    ``<repo>/satellite/tools/diagram.py``, así que el repo está dos niveles arriba.
    """
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "desktop" / "node_modules" / "pele" / "dist" / "pele.js"
    return candidate if candidate.is_file() else None


def _module_candidates(
    explicit: str | None, project_root: Path | None, node: str | None = None
) -> list[Path]:
    """Rutas candidatas a ``pele/dist/pele.js``, en orden de prioridad."""
    candidates: list[Path] = []
    if explicit:
        raw = Path(explicit).expanduser()
        if raw.suffix in (".js", ".mjs") or raw.is_file():
            candidates.append(raw)
        candidates.append(raw / "dist" / "pele.js")
        candidates.append(raw / "pele" / "dist" / "pele.js")
        candidates.append(raw / "node_modules" / "pele" / "dist" / "pele.js")
    if project_root is not None:
        candidates.append(Path(project_root) / "node_modules" / "pele" / "dist" / "pele.js")
    bundled = _self_bundled_module()
    if bundled is not None:
        candidates.append(bundled)
    for root in _global_module_roots(node):
        candidates.append(root / "pele" / "dist" / "pele.js")
    for entry in os.environ.get("NODE_PATH", "").split(os.pathsep):
        if entry.strip():
            candidates.append(Path(entry) / "pele" / "dist" / "pele.js")
    return candidates


def resolve_pele_module(
    project_root: Path | None = None, *, node: str | None = None
) -> Path | None:
    """Ubica el ``pele/dist/pele.js`` instalado por el usuario, o ``None``."""
    explicit = os.environ.get("SATELLITE_PELE_MODULE") or os.environ.get("SATELLITE_PELE_PATH")
    for candidate in _module_candidates(explicit, project_root, node):
        if candidate.is_file():
            return candidate
    return None


def pele_status(project_root: Path | None = None) -> PeleStatus:
    """Diagnóstico: qué falta para poder renderizar (Node / módulo pele)."""
    node = find_node()
    module = resolve_pele_module(project_root, node=node)
    return PeleStatus(
        node=node,
        module=module,
        details={"helper": str(HELPER_PATH), "helper_present": HELPER_PATH.is_file()},
    )


def render_mermaid(
    source: str,
    *,
    project_root: Path | None = None,
    out_path: str | None = None,
    options: dict | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    node: str | None = None,
    module: Path | None = None,
) -> RenderOutcome:
    """Renderiza ``source`` (Mermaid) a SVG usando el ``pele`` del usuario.

    Nunca instala nada. Si Node o ``pele`` faltan, devuelve un ``RenderOutcome`` con
    ``error_code="pele_unavailable"`` en vez de lanzar. ``node``/``module`` permiten
    inyectar valores en tests.
    """
    node = node or find_node()
    if node is None:
        return RenderOutcome(ok=False, error_code="pele_unavailable", error=SETUP_INSTRUCTIONS)
    module = module or resolve_pele_module(project_root, node=node)
    if module is None:
        return RenderOutcome(ok=False, error_code="pele_unavailable", error=SETUP_INSTRUCTIONS)
    if not HELPER_PATH.is_file():
        return RenderOutcome(
            ok=False,
            error_code="pele_unavailable",
            error=f"falta el helper de Pele: {HELPER_PATH}",
        )
    request: dict = {"module": str(module), "source": source}
    if out_path:
        request["outPath"] = str(out_path)
    if options:
        request["options"] = options
    # El PATH del proceso puede ser mínimo (app de macOS desde Finder); el helper
    # y sus hijos deben poder resolver el mismo node aunque no esté en el PATH.
    env = dict(os.environ)
    env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [node, str(HELPER_PATH)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return RenderOutcome(ok=False, error_code="pele_timeout", error="pele tardó demasiado")
    except OSError as exc:
        return RenderOutcome(ok=False, error_code="pele_failed", error=str(exc))
    payload = _parse_payload(proc.stdout)
    if payload is None:
        detail = (proc.stderr or proc.stdout or "sin salida").strip()[:2000]
        return RenderOutcome(ok=False, error_code="pele_failed", error=detail)
    if not payload.get("ok"):
        return RenderOutcome(
            ok=False,
            error_code=str(payload.get("code") or "pele_failed"),
            error=str(payload.get("error") or "Pele no pudo renderizar el diagrama"),
        )
    return RenderOutcome(
        ok=True,
        svg=str(payload.get("svg") or ""),
        diagram_type=str(payload.get("type") or ""),
        width=int(payload.get("width") or 0),
        height=int(payload.get("height") or 0),
    )


def _parse_payload(stdout: str) -> dict | None:
    """Extrae el objeto JSON de la salida del helper (tolerante a ruido)."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _default_out_path(source: str) -> str:
    """Ruta por defecto del artefacto: ``diagrams/<hash>.svg`` (relativa al proyecto)."""
    import hashlib

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{DEFAULT_DIAGRAM_DIR}/diagram-{digest}.svg"


class RenderDiagramTool(Tool):
    """Renderiza una fuente Mermaid a un SVG dentro del proyecto usando Pele."""

    @property
    def id(self) -> str:
        return "render_diagram"

    @property
    def description(self) -> str:
        return (
            "Renders a Mermaid diagram (flowchart, sequence, class, state, ER, gantt, "
            "mindmap, ...) to an SVG file inside the project, using the 'pele' renderer "
            "when it is installed. Returns the SVG path and markup. If Pele is not "
            "available it fails with error_code 'pele_unavailable' and echoes the "
            "Mermaid source so the caller can still present it."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("diagram",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "diagram": {
                    "type": "string",
                    "description": "Mermaid source for the diagram.",
                },
                "out_path": {
                    "type": "string",
                    "description": (
                        "SVG output path relative to the project "
                        "(default: diagrams/diagram-<hash>.svg)."
                    ),
                },
            },
            "required": ["diagram"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        source = arguments.get("diagram")
        if not isinstance(source, str) or not source.strip():
            return ToolResult(
                success=False,
                error="se requiere el argumento 'diagram' (fuente Mermaid)",
                error_code="invalid_arguments",
            )
        raw_out = arguments.get("out_path")
        if raw_out is not None and (not isinstance(raw_out, str) or not raw_out.strip()):
            raw_out = None
        path = _resolve(raw_out or _default_out_path(source), tool_ctx)
        previous = _read_previous(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(
                success=False,
                error=f"no se pudo crear el directorio de '{path}': {exc}",
                error_code="write_failed",
            )
        outcome = render_mermaid(
            source, project_root=tool_ctx.project_root, out_path=str(path)
        )
        if not outcome.ok:
            return ToolResult(
                success=False,
                error=outcome.error,
                error_code=outcome.error_code or "pele_failed",
                output={
                    "diagram": source,
                    "rendered": False,
                    "fallback": "mermaid",
                    "out_path": str(path),
                },
            )
        # Pele writes the SVG to outPath and omits it from its JSON response. Read the
        # artifact back so `svg`/`bytes` describe the real file instead of reporting 0.
        svg = outcome.svg
        if not svg:
            try:
                svg = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                svg = ""
        output = {
            "path": str(path),
            "diagram_type": outcome.diagram_type,
            "width": outcome.width,
            "height": outcome.height,
            "bytes": len(svg.encode("utf-8")),
            "rendered": True,
            "svg": svg,
        }
        if not _record_change(tool_ctx, path, previous):
            output["aviso"] = "no se pudo registrar el cambio para poder deshacerlo"
        return ToolResult(success=True, output=output)


__all__ = [
    "PeleStatus",
    "RenderDiagramTool",
    "RenderOutcome",
    "SETUP_INSTRUCTIONS",
    "find_node",
    "pele_status",
    "render_mermaid",
    "resolve_pele_module",
]
