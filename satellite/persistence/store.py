"""FileStore: persistence under .satellite/ with atomic writes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from satellite.core.atomic import atomic_write_text
from satellite.core.events import ExecutionEvent
from satellite.core.graph import ExecutionGraph
from satellite.core.task import Task
from satellite.kernel import migrations
from satellite.kernel.guard import KernelWriteError
from satellite.persistence.base import Store

SUBDIRS = ("tasks", "plans", "executions", "state", "context", "logs", "config", "agents")


class FileStore(Store):
    """JSON/JSONL persistence under project_root/.satellite/."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root / ".satellite"
        for sub in SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        # Cola y worker por bucle: una sola escritura a la vez evita reordenar
        # eventos cuando varios productores llaman a append_event en paralelo.
        self._event_queue: asyncio.Queue[
            tuple[str, ExecutionEvent, asyncio.Future[None]]
        ] | None = None
        self._event_worker: asyncio.Task[None] | None = None

    async def save_task(self, task: Task) -> None:
        self._write_json(self._task_path(task.task_id), task.to_dict())

    async def load_task(self, task_id: str) -> Task:
        path = self._task_path(task_id)
        if not path.exists():
            raise KeyError(f"task no encontrado: {task_id}")
        return Task.from_dict(self._read_json(path))

    async def save_graph(self, task_id: str, graph: ExecutionGraph) -> None:
        self._write_json(self._graph_path(task_id), graph.to_dict())

    async def load_graph(self, task_id: str) -> ExecutionGraph:
        path = self._graph_path(task_id)
        if not path.exists():
            raise KeyError(f"grafo no encontrado: {task_id}")
        return ExecutionGraph.from_dict(self._read_json(path))

    async def append_event(self, task_id: str, event: ExecutionEvent) -> None:
        loop = asyncio.get_running_loop()
        if (
            self._event_queue is None
            or self._event_worker is None
            or self._event_worker.done()
            or self._event_worker.get_loop() is not loop
        ):
            self._event_queue = asyncio.Queue()
            self._event_worker = loop.create_task(self._write_events(self._event_queue))
        done = loop.create_future()
        self._event_queue.put_nowait((task_id, event, done))
        await done

    async def _write_events(
        self,
        queue: asyncio.Queue[tuple[str, ExecutionEvent, asyncio.Future[None]]],
    ) -> None:
        """Escribe eventos en orden, fuera del event loop."""
        while True:
            task_id, event, done = await queue.get()
            try:
                await asyncio.to_thread(self._append_event_sync, task_id, event)
            except asyncio.CancelledError:
                if not done.done():
                    done.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 — se propaga al llamador
                if not done.done():
                    done.set_exception(exc)
            else:
                if not done.done():
                    done.set_result(None)
            finally:
                queue.task_done()

    def _append_event_sync(self, task_id: str, event: ExecutionEvent) -> None:
        path = self._events_path(task_id)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    async def load_events(self, task_id: str) -> list[ExecutionEvent]:
        return await asyncio.to_thread(self._load_events_sync, task_id)

    def _load_events_sync(self, task_id: str) -> list[ExecutionEvent]:
        path = self._events_path(task_id)
        events: list[ExecutionEvent] = []
        if not path.exists():
            return events
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(ExecutionEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                continue  # skip corrupted lines
        return events

    async def save_state(self, task_id: str, state: dict) -> None:
        self._write_json(self._state_path(task_id), state)

    async def load_state(self, task_id: str) -> dict | None:
        path = self._state_path(task_id)
        if not path.exists():
            return None
        return self._read_json(path)

    def list_tasks(self) -> list[str]:
        """List task ids present in the store (for CLI/TUI)."""
        return [p.stem for p in (self.root / "tasks").glob("*.json")]

    # --- esquema, snapshots y rollback ---

    def schema_version(self) -> int:
        """Versión de esquema del directorio de datos."""
        return migrations.current_version(self.root)

    def migrate(self) -> list[int]:
        """Aplica las migraciones de esquema pendientes."""
        return migrations.migrate(self.root)

    def snapshot(self, label: str = "") -> Path:
        """Crea un snapshot del directorio de datos antes de cambios masivos."""
        return migrations.create_snapshot(self.root, label=label)

    def list_snapshots(self) -> list[Path]:
        """Snapshots disponibles."""
        return migrations.list_snapshots(self.root)

    def restore(self, name: str) -> None:
        """Restaura el directorio de datos desde un snapshot."""
        migrations.restore_snapshot(self.root, name)

    # --- helpers ---

    def _task_path(self, task_id: str) -> Path:
        return self.root / "tasks" / f"{task_id}.json"

    def _graph_path(self, task_id: str) -> Path:
        return self.root / "plans" / f"{task_id}-graph.json"

    def _events_path(self, task_id: str) -> Path:
        return self.root / "executions" / f"{task_id}.jsonl"

    def _state_path(self, task_id: str) -> Path:
        return self.root / "state" / f"{task_id}.json"

    def _write_json(self, path: Path, data: dict) -> None:
        # El store escribe su propio estado, no ficheros del framework: si un
        # ``root`` manipulado apunta al paquete, se corta aquí (misma regla que
        # el guard, sin importarlo para no crear un ciclo).
        target = Path(path).resolve()
        package = Path(__file__).resolve().parents[1]
        if target == package or package in target.parents:
            raise KernelWriteError(f"el store no escribe dentro del paquete: {target}")
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))
