"""Tool ``render_diagram`` (Pele): éxito, artefacto en disco y respaldo si falta Pele."""

import asyncio
from pathlib import Path

from satellite.kernel.manifest import RESERVED_SKILL_IDS
from satellite.tools import diagram as diagram_module
from satellite.tools.base import ToolContext
from satellite.tools.diagram import (
    RenderDiagramTool,
    RenderOutcome,
    pele_status,
    resolve_pele_module,
)


def run(coro):
    return asyncio.run(coro)


def test_tool_id_is_reserved_kernel_tool() -> None:
    assert RenderDiagramTool().id == "render_diagram"
    assert RenderDiagramTool().id in RESERVED_SKILL_IDS


def test_missing_diagram_argument(tmp_path: Path) -> None:
    ctx = ToolContext(project_root=tmp_path)
    result = run(RenderDiagramTool().execute({}, ctx))
    assert result.success is False
    assert result.error_code == "invalid_arguments"


def test_render_success_writes_artifact(tmp_path: Path, monkeypatch) -> None:
    def fake_render(source, *, project_root=None, out_path=None, **kwargs):
        Path(out_path).write_text("<svg>ok</svg>", encoding="utf-8")
        return RenderOutcome(
            ok=True, svg="<svg>ok</svg>", diagram_type="flowchart", width=10, height=5
        )

    monkeypatch.setattr(diagram_module, "render_mermaid", fake_render)
    ctx = ToolContext(project_root=tmp_path)
    result = run(RenderDiagramTool().execute({"diagram": "flowchart LR\n A --> B"}, ctx))

    assert result.success is True
    assert result.output["rendered"] is True
    assert result.output["diagram_type"] == "flowchart"
    artifact = Path(result.output["path"])
    assert artifact.is_file()
    assert artifact.read_text(encoding="utf-8") == "<svg>ok</svg>"
    assert tmp_path in artifact.parents


def test_render_reads_the_artifact_when_pele_only_writes_the_file(
    tmp_path: Path, monkeypatch
) -> None:
    """Pele escribe en outPath y no devuelve `svg`: el tool debe leer el archivo."""

    def fake_render(source, *, project_root=None, out_path=None, **kwargs):
        Path(out_path).write_text("<svg>from-file</svg>", encoding="utf-8")
        return RenderOutcome(ok=True, svg="", diagram_type="flowchart")

    monkeypatch.setattr(diagram_module, "render_mermaid", fake_render)
    ctx = ToolContext(project_root=tmp_path)
    result = run(RenderDiagramTool().execute({"diagram": "flowchart LR\n A --> B"}, ctx))

    assert result.success is True
    assert result.output["svg"] == "<svg>from-file</svg>"
    assert result.output["bytes"] == len("<svg>from-file</svg>".encode("utf-8"))


def test_render_unavailable_falls_back_to_mermaid(tmp_path: Path, monkeypatch) -> None:
    def fake_render(source, *, project_root=None, out_path=None, **kwargs):
        return RenderOutcome(ok=False, error_code="pele_unavailable", error="no hay pele")

    monkeypatch.setattr(diagram_module, "render_mermaid", fake_render)
    ctx = ToolContext(project_root=tmp_path)
    source = "flowchart LR\n A --> B"
    result = run(RenderDiagramTool().execute({"diagram": source}, ctx))

    assert result.success is False
    assert result.error_code == "pele_unavailable"
    assert result.output["rendered"] is False
    assert result.output["diagram"] == source
    assert result.output["fallback"] == "mermaid"


def test_resolve_pele_module_from_env(tmp_path: Path, monkeypatch) -> None:
    fake_module = tmp_path / "node_modules" / "pele" / "dist" / "pele.js"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("export const render = () => {};", encoding="utf-8")
    monkeypatch.setenv("SATELLITE_PELE_PATH", str(tmp_path))
    monkeypatch.delenv("SATELLITE_PELE_MODULE", raising=False)

    assert resolve_pele_module(tmp_path) == fake_module


def test_find_node_falls_back_to_common_locations(tmp_path: Path, monkeypatch) -> None:
    """Una app de macOS sin PATH de shell no ve nvm: se buscan rutas habituales."""
    fake = tmp_path / "bin" / "node"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(diagram_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(diagram_module, "_candidate_node_paths", lambda: [str(fake)])
    assert diagram_module.find_node() == str(fake)


def test_pele_status_reports_missing_node(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SATELLITE_PELE_MODULE", raising=False)
    monkeypatch.delenv("SATELLITE_PELE_PATH", raising=False)
    monkeypatch.setattr(diagram_module, "find_node", lambda: None)
    status = pele_status(tmp_path)
    assert status.available is False
    assert status.node is None
