"""Cierre central de la lectura de rutas fuera del proyecto (contexto).

Comprueba que el resolver bloquea por defecto cualquier PATH/DIR que escape de la
raíz (``..``, absolutas, symlinks/junctions), que una aprobación puntual sólo
habilita la ruta aprobada para esa petición, y que no se filtra a la siguiente.
"""

import asyncio
from pathlib import Path

import pytest

from satellite.approval.base import (
    ApprovalPort,
    AutoApprovalPort,
    DenyAllApprovalPort,
)
from satellite.context.engine import DefaultContextEngine
from satellite.context.resolver import ContextResolver
from satellite.core.context_models import ContextRef, ContextRefKind
from satellite.core.proposal import ApprovalDecision, ApprovalStatus
from satellite.tools.base import ToolContext
from satellite.tools.context_tool import RequestContextTool


def run(coro):
    return asyncio.run(coro)


def ref(value: str, kind: ContextRefKind = ContextRefKind.PATH) -> ContextRef:
    return ContextRef(kind=kind, value=value)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("dentro", encoding="utf-8")
    return project


def make_dir_link(link: Path, target: Path) -> bool:
    """Enlace de directorio: symlink o, en Windows sin privilegios, junction."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(target), str(link))
        return True
    except Exception:  # noqa: BLE001 — sin privilegios no se puede crear el enlace
        return False


# --- resolver: confinamiento por defecto ---


def test_internal_path_is_read(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    resolver = ContextResolver(project)

    section = run(resolver.resolve_ref(ref("a.txt")))

    assert section is not None and section.content == "dentro"


def test_parent_traversal_is_blocked(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (tmp_path / "secreto.txt").write_text("fuera", encoding="utf-8")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref("../secreto.txt"))) is None


def test_absolute_outside_is_blocked_without_approval(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("fuera", encoding="utf-8")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref(str(outside)))) is None


def test_dir_outside_is_blocked_without_approval(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "otro"
    outside.mkdir()
    (outside / "x.txt").write_text("x", encoding="utf-8")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref(str(outside), ContextRefKind.DIR))) is None


def test_symlink_file_escape_is_blocked(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("fuera", encoding="utf-8")
    link = project / "enlace.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("la plataforma no permite crear symlinks")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref("enlace.txt"))) is None


def test_symlink_dir_escape_is_blocked(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside_dir = tmp_path / "otro"
    outside_dir.mkdir()
    (outside_dir / "x.txt").write_text("x", encoding="utf-8")
    link = project / "enlace"
    if not make_dir_link(link, outside_dir):
        pytest.skip("la plataforma no permite crear symlinks/junctions")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref("enlace", ContextRefKind.DIR))) is None
    assert run(resolver.resolve_ref(ref("enlace/x.txt"))) is None


# --- resolver: autorización puntual ---


def test_approval_grants_only_the_approved_path(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    approved = tmp_path / "aprobado.txt"
    approved.write_text("ok", encoding="utf-8")
    sister = tmp_path / "hermana.txt"
    sister.write_text("no", encoding="utf-8")
    resolver = ContextResolver(project)
    allowed = (approved,)

    section = run(resolver.resolve_ref(ref(str(approved)), None, allowed))
    assert section is not None and section.content == "ok"
    # Otra ruta fuera, aunque sea hermana, sigue bloqueada.
    assert run(resolver.resolve_ref(ref(str(sister)), None, allowed)) is None
    # Y una ruta interna sigue leyéndose sin aprobación.
    assert run(resolver.resolve_ref(ref("a.txt"))).content == "dentro"


def test_approved_dir_grants_its_subtree(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside_dir = tmp_path / "otro"
    outside_dir.mkdir()
    (outside_dir / "child.txt").write_text("hijo", encoding="utf-8")
    resolver = ContextResolver(project)
    allowed = (outside_dir,)

    listing = run(resolver.resolve_ref(ref(str(outside_dir), ContextRefKind.DIR), None, allowed))
    child = run(resolver.resolve_ref(ref(str(outside_dir / "child.txt")), None, allowed))

    assert listing is not None and "child.txt" in listing.content
    assert child is not None and child.content == "hijo"


def test_approval_does_not_leak_to_the_next_request(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("fuera", encoding="utf-8")
    resolver = ContextResolver(project)

    assert run(resolver.resolve_ref(ref(str(outside)), None, (outside,))) is not None
    # Sin la autorización de esta petición, la misma ruta vuelve a estar vedada.
    assert run(resolver.resolve_ref(ref(str(outside)))) is None


def test_approval_does_not_leak_between_concurrent_requests(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("fuera", encoding="utf-8")
    resolver = ContextResolver(project)

    async def scenario():
        return await asyncio.gather(
            resolver.resolve_ref(ref(str(outside)), None, (outside,)),
            resolver.resolve_ref(ref(str(outside))),
        )

    con_aprobacion, sin_aprobacion = run(scenario())
    assert con_aprobacion is not None
    assert sin_aprobacion is None


# --- tool: la UX de aprobación se conserva ---


def test_tool_reads_approved_outside_path(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("aprobado", encoding="utf-8")
    ctx = ToolContext(project_root=project, approval=AutoApprovalPort())
    ctx.engine = DefaultContextEngine(project)

    result = run(RequestContextTool().execute(
        {"refs": [{"kind": "path", "value": str(outside)}]}, ctx
    ))

    assert result.success is True
    assert result.output["sections"][0]["content"] == "aprobado"


def test_tool_denies_outside_without_approval(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("x", encoding="utf-8")
    ctx = ToolContext(project_root=project, approval=DenyAllApprovalPort())
    ctx.engine = DefaultContextEngine(project)

    result = run(RequestContextTool().execute(
        {"refs": [{"kind": "path", "value": str(outside)}]}, ctx
    ))

    assert result.success is False
    assert "denegada" in (result.error or "")


def test_tool_requires_an_approval_port_for_outside(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("x", encoding="utf-8")
    ctx = ToolContext(project_root=project)  # sin puerto de aprobación
    ctx.engine = DefaultContextEngine(project)

    result = run(RequestContextTool().execute(
        {"refs": [{"kind": "path", "value": str(outside)}]}, ctx
    ))

    assert result.success is False
    assert "permiso requerido" in (result.error or "")


def test_tool_does_not_ask_for_internal_paths(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    class _Spy(ApprovalPort):
        def __init__(self) -> None:
            self.calls = 0

        async def request(self, proposal, ctx) -> ApprovalDecision:
            self.calls += 1
            return ApprovalDecision(status=ApprovalStatus.APPROVED, reason="spy")

    spy = _Spy()
    ctx = ToolContext(project_root=project, approval=spy)
    ctx.engine = DefaultContextEngine(project)

    result = run(RequestContextTool().execute(
        {"refs": [{"kind": "path", "value": "a.txt"}]}, ctx
    ))

    assert result.success is True
    assert result.output["sections"][0]["content"] == "dentro"
    assert spy.calls == 0


def test_tool_does_not_leak_approval_between_calls(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "secreto.txt"
    outside.write_text("fuera", encoding="utf-8")
    engine = DefaultContextEngine(project)
    tool = RequestContextTool()
    approved_ctx = ToolContext(project_root=project, approval=AutoApprovalPort())
    approved_ctx.engine = engine
    denied_ctx = ToolContext(project_root=project, approval=DenyAllApprovalPort())
    denied_ctx.engine = engine

    primero = run(tool.execute({"refs": [{"kind": "path", "value": str(outside)}]}, approved_ctx))
    segundo = run(tool.execute({"refs": [{"kind": "path", "value": str(outside)}]}, denied_ctx))

    assert primero.success is True
    assert segundo.success is False
