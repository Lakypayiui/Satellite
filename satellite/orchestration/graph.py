"""Grafo de ejecución: un DAG de delegaciones en agentes del catálogo.

El piloto, después de explorar el código real, puede emitir uno de estos grafos en vez de
delegar de una en una: nodos que delegan en un agente, con dependencias entre ellos, que
un ejecutor corre en olas paralelas (docs/PLAN-grafo-piloto.md). Este módulo es sólo el
**modelo** y su validación; la ejecución vive en ``GraphExecutor`` (fase 1).

Es la capa de orquestación, no ``core``: un nodo nombra un ``agent_id`` del catálogo, así
que depende del dominio, a diferencia de los contratos puros de ``core``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Cuándo corre un nodo según cómo terminaron sus dependencias.
RUN_IF = ("all_ok", "any_done")
DEFAULT_RUN_IF = "all_ok"


class GraphError(ValueError):
    """Grafo inválido: ids duplicados, dependencia inexistente, ciclo o límite."""


@dataclass(frozen=True)
class GraphLimits:
    """Topes del grafo entero (red de seguridad, no objetivos)."""

    max_nodes: int = 40
    max_retries_total: int = 20
    timeout_s: int = 1800

    def validate(self) -> None:
        if self.max_nodes <= 0:
            raise GraphError("max_nodes debe ser > 0")
        if self.max_retries_total < 0:
            raise GraphError("max_retries_total debe ser >= 0")
        if self.timeout_s <= 0:
            raise GraphError("timeout_s debe ser > 0")

    def to_dict(self) -> dict:
        return {
            "max_nodes": self.max_nodes,
            "max_retries_total": self.max_retries_total,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "GraphLimits":
        d = data or {}
        base = cls()
        return cls(
            max_nodes=int(d.get("max_nodes", base.max_nodes)),
            max_retries_total=int(d.get("max_retries_total", base.max_retries_total)),
            timeout_s=int(d.get("timeout_s", base.timeout_s)),
        )


@dataclass(frozen=True)
class GraphNode:
    """Un nodo: delega ``task`` en ``agent_id`` cuando sus ``depends_on`` están listos."""

    id: str
    agent_id: str
    task: dict = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    max_retries: int = 1
    # ``all_ok``: corre si todas sus dependencias terminaron OK (puerta de verificación).
    # ``any_done``: corre en cuanto todas terminaron, con éxito o no (p. ej. un informe).
    run_if: str = DEFAULT_RUN_IF

    def validate(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise GraphError("un nodo necesita un id no vacío")
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise GraphError(f"nodo '{self.id}': agent_id no puede estar vacío")
        if not isinstance(self.task, dict):
            raise GraphError(f"nodo '{self.id}': task debe ser un objeto")
        if self.id in self.depends_on:
            raise GraphError(f"nodo '{self.id}': no puede depender de sí mismo")
        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise GraphError(f"nodo '{self.id}': max_retries debe ser int >= 0")
        if self.run_if not in RUN_IF:
            raise GraphError(f"nodo '{self.id}': run_if debe ser uno de {RUN_IF}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "task": dict(self.task),
            "depends_on": list(self.depends_on),
            "max_retries": self.max_retries,
            "run_if": self.run_if,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphNode":
        if not isinstance(data, dict):
            raise GraphError("un nodo debe ser un objeto")
        depends = data.get("depends_on") or []
        if not isinstance(depends, (list, tuple)):
            raise GraphError(f"nodo '{data.get('id')}': depends_on debe ser una lista")
        return cls(
            id=str(data.get("id", "")),
            agent_id=str(data.get("agent_id", "")),
            task=dict(data.get("task") or {}),
            depends_on=tuple(str(dep) for dep in depends),
            max_retries=int(data.get("max_retries", 1)),
            run_if=str(data.get("run_if") or DEFAULT_RUN_IF),
        )


@dataclass(frozen=True)
class ExecutionGraph:
    """DAG de delegaciones. ``validate`` garantiza que se puede ejecutar por olas."""

    nodes: tuple[GraphNode, ...]
    limits: GraphLimits = field(default_factory=GraphLimits)

    def validate(self) -> None:
        self.limits.validate()
        if not self.nodes:
            raise GraphError("el grafo no tiene nodos")
        if len(self.nodes) > self.limits.max_nodes:
            raise GraphError(
                f"el grafo tiene {len(self.nodes)} nodos (máximo {self.limits.max_nodes})"
            )
        ids: set[str] = set()
        for node in self.nodes:
            node.validate()
            if node.id in ids:
                raise GraphError(f"id de nodo duplicado: '{node.id}'")
            ids.add(node.id)
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in ids:
                    raise GraphError(f"nodo '{node.id}': depende de '{dep}', que no existe")
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        """Orden topológico de Kahn; lo que sobra está en un ciclo."""
        pending = {node.id: set(node.depends_on) for node in self.nodes}
        resolved: set[str] = set()
        while True:
            ready = [nid for nid, deps in pending.items() if deps <= resolved]
            ready = [nid for nid in ready if nid not in resolved]
            if not ready:
                break
            resolved.update(ready)
        if len(resolved) != len(self.nodes):
            ciclo = sorted(set(pending) - resolved)
            raise GraphError(f"el grafo tiene un ciclo entre: {', '.join(ciclo)}")

    def node(self, node_id: str) -> GraphNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def ready(self, done: set[str], started: set[str]) -> list[GraphNode]:
        """Nodos ejecutables ya: no empezados y con todas sus dependencias terminadas."""
        return [
            node
            for node in self.nodes
            if node.id not in started
            and node.id not in done
            and set(node.depends_on) <= done
        ]

    def to_dict(self) -> dict:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "limits": self.limits.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionGraph":
        if not isinstance(data, dict):
            raise GraphError("el grafo debe ser un objeto")
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list):
            raise GraphError("el grafo necesita una lista 'nodes'")
        graph = cls(
            nodes=tuple(GraphNode.from_dict(item) for item in raw_nodes),
            limits=GraphLimits.from_dict(data.get("limits")),
        )
        graph.validate()
        return graph


__all__ = ["ExecutionGraph", "GraphNode", "GraphLimits", "GraphError", "RUN_IF"]
