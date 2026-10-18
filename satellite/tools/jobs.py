"""Trabajos en segundo plano: comandos largos que siguen corriendo.

Un job se lanza, deja su salida en ``state/jobs/<id>.log`` y su ficha en
``state/jobs/<id>.json``, y sigue su camino mientras el agente puede consultarlo,
esperarlo o pararlo. Quien puede controlarlo es el proceso que lo lanzó: si otro proceso
mira un job ajeno, se dice que no tiene control sobre él en vez de fingir que sí.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from satellite.core.tool_models import ToolResult
from satellite.kernel.paths import resolve_paths
from satellite.sandbox.process import group_kwargs, terminate_tree_sync
from satellite.tools.base import Tool, ToolContext

JOB_DIR = "jobs"
OUTPUT_TAIL_CHARS = 64_000
STOP_TIMEOUT_S = 5
_REGISTRIES: dict[str, "JobRegistry"] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobInfo:
    """Ficha de un trabajo."""

    id: str
    command: str
    pid: int = 0
    status: str = "running"
    exit_code: int | None = None
    started_at: str = ""
    cwd: str = ""

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "command": self.command,
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JobInfo":
        exit_code = d.get("exit_code")
        return cls(
            id=str(d.get("job_id") or d.get("id") or ""),
            command=str(d.get("command") or ""),
            pid=int(d.get("pid") or 0),
            status=str(d.get("status") or "desconocido"),
            exit_code=int(exit_code) if isinstance(exit_code, int) else None,
            started_at=str(d.get("started_at") or ""),
            cwd=str(d.get("cwd") or ""),
        )


def registry_for(project_root) -> "JobRegistry":
    """Registro del proyecto: **uno por proceso**, que es quien puede controlarlos."""
    key = str(Path(project_root or ".").resolve())
    registry = _REGISTRIES.get(key)
    if registry is None:
        registry = JobRegistry(project_root)
        _REGISTRIES[key] = registry
    return registry


class JobRegistry:
    """Lanza trabajos, sigue su estado y los para."""

    def __init__(self, project_root) -> None:
        self.project_root = Path(project_root or ".")
        data = resolve_paths(self.project_root).project_data
        self.root = Path(data or (self.project_root / ".satellite")) / "state" / JOB_DIR
        self._procs: dict[str, subprocess.Popen] = {}

    # --- lanzar ---

    def start(self, argv: list[str], *, cwd, env: dict, command: str = "") -> JobInfo:
        """Lanza el comando sin esperarlo y devuelve su ficha."""
        self.root.mkdir(parents=True, exist_ok=True)
        job_id = uuid4().hex[:8]
        with self.log_path(job_id).open("w", encoding="utf-8", errors="replace") as handle:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                **group_kwargs(),
            )
        info = JobInfo(
            id=job_id,
            command=command or " ".join(argv),
            pid=proc.pid,
            status="running",
            started_at=_now(),
            cwd=str(cwd),
        )
        self._procs[job_id] = proc
        self._write(info)
        return info

    def log_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.log"

    def output(self, job_id: str, *, tail: int = OUTPUT_TAIL_CHARS) -> str:
        """Lo que lleva escrito el trabajo (recortado por el principio si es enorme)."""
        try:
            text = self.log_path(job_id).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"(no se pudo leer la salida: {exc})"
        if len(text) > tail:
            return f"[… {len(text) - tail} caracteres omitidos al principio]\n" + text[-tail:]
        return text

    # --- estado ---

    def status(self, job_id: str) -> JobInfo | None:
        """Ficha actualizada; ``None`` si ese trabajo no existe."""
        info = self._read(job_id)
        proc = self._procs.get(job_id)
        if proc is None:
            if info is None:
                return None
            if info.status == "running":
                # lo lanzó otro proceso: desde aquí no se puede saber si sigue vivo
                return replace(info, status="desconocido")
            return info
        if info.status == "stopped":
            return info  # una vez parado sigue parado: no se convierte en «failed»
        code = proc.poll()
        if code is None:
            info = replace(info, status="running", exit_code=None)
        elif code == 0:
            info = replace(info, status="done", exit_code=0)
        else:
            info = replace(info, status="failed", exit_code=code)
        self._write(info)
        return info

    def list(self) -> list[JobInfo]:
        """Todos los trabajos conocidos, del más reciente al más antiguo."""
        infos: list[JobInfo] = []
        if self.root.is_dir():
            for path in self.root.glob("*.json"):
                info = self.status(path.stem)
                if info is not None:
                    infos.append(info)
        infos.sort(key=lambda info: info.started_at, reverse=True)
        return infos

    # --- esperar / parar ---

    async def wait(self, job_id: str, timeout_s: float) -> tuple[JobInfo | None, str]:
        """Espera a que termine. Devuelve ``(ficha, "done"|"timeout"|"ajeno")``.

        Se sondea el estado en vez de esperar al proceso en un hilo: un hilo no se puede
        cancelar, así que el timeout no se respetaba y la espera se quedaba colgada hasta
        que el comando acabara, que es justo lo que no queremos con un comando largo.
        """
        info = self.status(job_id)
        if info is None:
            return None, "ajeno"
        if self._procs.get(job_id) is None:
            return info, "ajeno" if info.status in ("running", "desconocido") else "done"
        loop = asyncio.get_running_loop()
        limite = loop.time() + max(0.0, float(timeout_s))
        while True:
            info = self.status(job_id)
            if info is None or info.status != "running":
                return info, "done"
            if loop.time() >= limite:
                return info, "timeout"
            await asyncio.sleep(0.1)

    def stop(self, job_id: str) -> JobInfo | None:
        """Para el trabajo; ``None`` si lo lanzó otro proceso."""
        proc = self._procs.get(job_id)
        if proc is None:
            return None
        if proc.poll() is not None:
            # ya había terminado solo: no se «para» nada, y no se dice que sí
            return self.status(job_id)
        # Parar un comando largo debe terminar también a sus descendientes: si no, lo que
        # bloqueaba (un servidor, un watcher hijo) sigue vivo aunque el job diga "stopped".
        terminate_tree_sync(proc, grace=STOP_TIMEOUT_S)
        info = replace(self.status(job_id) or JobInfo(id=job_id, command=""), status="stopped")
        self._write(info)
        return info

    # --- ficheros ---

    def _read(self, job_id: str) -> JobInfo | None:
        try:
            data = json.loads((self.root / f"{job_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        info = JobInfo.from_dict(data)
        return info if info.id else None

    def _write(self, info: JobInfo) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / f"{info.id}.json").write_text(
                json.dumps(info.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            return


class JobTool(Tool):
    """Consulta, espera o para los comandos lanzados en segundo plano."""

    @property
    def id(self) -> str:
        return "job"

    @property
    def description(self) -> str:
        return (
            "Gestiona los trabajos en segundo plano (los que lanza run_command con "
            "background=true): action=list|status|output|wait|stop."
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return ("terminal",)

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "status", "output", "wait", "stop"],
                    "description": "Qué hacer con el trabajo.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Id del trabajo (lo devuelve run_command con background).",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Segundos a esperar con action=wait (default 30).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, arguments: dict, tool_ctx: ToolContext) -> ToolResult:
        action = str(arguments.get("action") or "").strip().lower()
        job_id = str(arguments.get("job_id") or "").strip()
        registry = registry_for(tool_ctx.project_root)
        if action == "list":
            infos = registry.list()
            return ToolResult(success=True, output=[info.to_dict() for info in infos])
        if not job_id:
            return ToolResult(success=False, error="se requiere 'job_id' para esa acción")
        if action == "status":
            info = registry.status(job_id)
            if info is None:
                return ToolResult(success=False, error=f"no existe el trabajo '{job_id}'")
            return ToolResult(success=True, output=info.to_dict())
        if action == "output":
            info = registry.status(job_id)
            if info is None:
                return ToolResult(success=False, error=f"no existe el trabajo '{job_id}'")
            return ToolResult(
                success=True,
                output={
                    "job_id": job_id,
                    "status": info.status,
                    "exit_code": info.exit_code,
                    "output": registry.output(job_id),
                },
            )
        if action == "wait":
            timeout_s = int(arguments.get("timeout_s", 30))
            info, estado = await registry.wait(job_id, timeout_s)
            if info is None:
                return ToolResult(success=False, error=f"no existe el trabajo '{job_id}'")
            if estado == "ajeno":
                return ToolResult(
                    success=False,
                    error=f"no puedo esperar el trabajo '{job_id}': lo lanzó otro proceso",
                    output=info.to_dict(),
                )
            if estado == "timeout":
                return ToolResult(
                    success=False,
                    error=f"sigue en curso tras {timeout_s}s",
                    output=info.to_dict(),
                )
            return ToolResult(
                success=True, output={**info.to_dict(), "output": registry.output(job_id)}
            )
        if action == "stop":
            info = registry.stop(job_id)
            if info is None:
                return ToolResult(
                    success=False,
                    error=f"no puedo parar el trabajo '{job_id}': lo lanzó otro proceso",
                )
            return ToolResult(success=True, output=info.to_dict())
        return ToolResult(success=False, error=f"acción desconocida: '{action}'")


def start_background(cmd_raw: str, argv: list[str], *, cwd, env: dict, project_root) -> ToolResult:
    """Lanza ``argv`` en segundo plano y devuelve su ficha como resultado de tool."""
    info = registry_for(project_root).start(argv, cwd=cwd, env=env, command=cmd_raw)
    return ToolResult(
        success=True,
        output={
            "job_id": info.id,
            "pid": info.pid,
            "command": info.command,
            "nota": "sigue en segundo plano: usa la tool 'job' (action=output|wait|stop)",
        },
    )
