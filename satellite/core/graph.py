"""ExecutionGraph: nodos, aristas condicionales y límites globales."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from satellite.core.errors import ContractError, GraphValidationError
from satellite.core.result import AgentResult, AgentStatus
from satellite.core.context_models import ContextRef

_VALID_OPERATORS = ("eq", "ne", "gt", "ge", "lt", "le")
_VALID_PAYLOADS = ("result", "feedback", "context", "none")


def _check_non_empty_str(value: Any, field_name: str, owner: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{owner}: {field_name} no puede estar vacío")
    return value


@dataclass(frozen=True)
class EdgeCondition:
    """Condición ESTRUCTURADA y determinista para elegir una arista."""

    type: str  # "status" | "field"
    value: Any = None
    field: str | None = None
    operator: str | None = None

    def __post_init__(self) -> None:
        if self.type not in ("status", "field"):
            raise ContractError(
                f"EdgeCondition: type inválido {self.type!r} (esperado 'status'|'field')"
            )
        if self.type == "status":
            if self.field is not None or self.operator is not None:
                raise ContractError(
                    "EdgeCondition: type='status' no admite field ni operator"
                )
        else:  # field
            if not self.field:
                raise ContractError(
                    "EdgeCondition: type='field' requiere field no vacío"
                )
            if self.operator not in _VALID_OPERATORS:
                raise ContractError(
                    f"EdgeCondition: operator inválido {self.operator!r} "
                    f"(esperado {_VALID_OPERATORS})"
                )

    def to_dict(self) -> dict:
        """Serializa la condición a dict JSON-serializable."""
        return {
            "type": self.type,
            "value": self.value,
            "field": self.field,
            "operator": self.operator,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EdgeCondition":
        """Construye la condición desde un dict."""
        return cls(
            type=d["type"],
            value=d.get("value"),
            field=d.get("field"),
            operator=d.get("operator"),
        )

    @classmethod
    def on_status(cls, status_value: str) -> "EdgeCondition":
        """Condición por estado: coincide si el resultado tiene ese estado."""
        return cls(type="status", value=status_value)

    @classmethod
    def on_field(cls, field: str, operator: str, value: Any) -> "EdgeCondition":
        """Condición por campo del resultado (status/summary/output.<ruta>)."""
        return cls(type="field", field=field, operator=operator, value=value)

    def matches(self, result: AgentResult) -> bool:
        """Evalúa la condición contra un resultado (determinista)."""
        if self.type == "status":
            expected = self.value
            if isinstance(expected, AgentStatus):
                expected = expected.value
            return result.status.value == str(expected)
        # type == "field"
        return _match_field(result, self.field, self.operator, self.value)


def _match_field(result: AgentResult, field_path: str, operator: str, expected: Any) -> bool:
    try:
        value = _extract_field(result, field_path)
    except (KeyError, AttributeError, TypeError, ValueError, IndexError):
        return False
    try:
        if operator == "eq":
            return value == expected
        if operator == "ne":
            return value != expected
        if operator == "gt":
            return value > expected
        if operator == "ge":
            return value >= expected
        if operator == "lt":
            return value < expected
        if operator == "le":
            return value <= expected
    except TypeError:
        return False
    return False


def _extract_field(result: AgentResult, field_path: str) -> Any:
    """Extrae 'status' | 'summary' | 'output.<a>.<b>' de un AgentResult."""
    if field_path == "status":
        return result.status.value
    if field_path == "summary":
        return result.summary
    if field_path.startswith("output."):
        current: Any = result.output
        for part in field_path[len("output."):].split("."):
            current = current[part]
        return current
    raise KeyError(f"campo desconocido: {field_path}")


@dataclass(frozen=True)
class Node:
    """Instancia CONCRETA de trabajo que ejecuta una capacidad."""

    id: str
    spec_id: str
    task: dict = field(default_factory=dict)
    context_refs: tuple[ContextRef, ...] = ()
    max_retries: int = 2
    model_role: str | None = None

    def __post_init__(self) -> None:
        _check_non_empty_str(self.id, "id", "Node")
        _check_non_empty_str(self.spec_id, "spec_id", "Node")
        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ContractError(
                f"Node '{self.id}': max_retries debe ser int >= 0, recibido {self.max_retries!r}"
            )
        if not isinstance(self.context_refs, tuple):
            raise ContractError(
                f"Node '{self.id}': context_refs debe ser tupla"
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "spec_id": self.spec_id,
            "task": dict(self.task),
            "context_refs": [r.to_dict() for r in self.context_refs],
            "max_retries": self.max_retries,
            "model_role": self.model_role,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=d["id"],
            spec_id=d["spec_id"],
            task=dict(d.get("task", {})),
            context_refs=tuple(
                ContextRef.from_dict(r) for r in d.get("context_refs", [])
            ),
            max_retries=int(d.get("max_retries", 2)),
            model_role=d.get("model_role"),
        )


@dataclass(frozen=True)
class Edge:
    """Conexión directa entre dos nodos."""

    id: str
    source: str
    target: str
    condition: EdgeCondition | None = None
    payload: str = "result"
    mapping: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_non_empty_str(self.id, "id", "Edge")
        _check_non_empty_str(self.source, "source", "Edge")
        _check_non_empty_str(self.target, "target", "Edge")
        if self.payload not in _VALID_PAYLOADS:
            raise ContractError(
                f"Edge '{self.id}': payload inválido {self.payload!r} "
                f"(esperado {_VALID_PAYLOADS})"
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "condition": self.condition.to_dict() if self.condition else None,
            "payload": self.payload,
            "mapping": dict(self.mapping),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        cond = d.get("condition")
        return cls(
            id=d["id"],
            source=d["source"],
            target=d["target"],
            condition=EdgeCondition.from_dict(cond) if cond else None,
            payload=d.get("payload", "result"),
            mapping=dict(d.get("mapping", {})),
        )


@dataclass(frozen=True)
class GraphLimits:
    """Límites globales del grafo (acotan ciclos y trabajo)."""

    max_total_steps: int = 100
    max_node_visits: int = 10
    max_retries_global: int = 10
    timeout_s: int = 600
    token_budget: int | None = None

    def __post_init__(self) -> None:
        if self.max_total_steps <= 0:
            raise ContractError("GraphLimits: max_total_steps debe ser > 0")
        if self.max_node_visits <= 0:
            raise ContractError("GraphLimits: max_node_visits debe ser > 0")
        if self.max_retries_global < 0:
            raise ContractError("GraphLimits: max_retries_global debe ser >= 0")
        if self.timeout_s <= 0:
            raise ContractError("GraphLimits: timeout_s debe ser > 0")

    def to_dict(self) -> dict:
        return {
            "max_total_steps": self.max_total_steps,
            "max_node_visits": self.max_node_visits,
            "max_retries_global": self.max_retries_global,
            "timeout_s": self.timeout_s,
            "token_budget": self.token_budget,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphLimits":
        return cls(
            max_total_steps=int(d.get("max_total_steps", 100)),
            max_node_visits=int(d.get("max_node_visits", 10)),
            max_retries_global=int(d.get("max_retries_global", 10)),
            timeout_s=int(d.get("timeout_s", 600)),
            token_budget=d.get("token_budget"),
        )


@dataclass
class ExecutionGraph:
    """Grafo de ejecución (construcción incremental; NO frozen)."""

    entry_node: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    limits: GraphLimits = field(default_factory=GraphLimits)

    def add_node(self, node: Node) -> None:
        """Agrega un nodo; lanza si el id ya existe."""
        if node.id in self.nodes:
            raise GraphValidationError(f"ExecutionGraph: nodo duplicado '{node.id}'")
        if not isinstance(node, Node):
            raise GraphValidationError(
                f"ExecutionGraph: add_node espera Node, recibido {type(node).__name__}"
            )
        self.nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        """Agrega una arista al grafo."""
        if not isinstance(edge, Edge):
            raise GraphValidationError(
                f"ExecutionGraph: add_edge espera Edge, recibido {type(edge).__name__}"
            )
        self.edges.append(edge)

    def successors(self, node_id: str, result: AgentResult) -> list[Edge]:
        """Aristas salientes de node_id cuya condición coincide (None = siempre)."""
        return [
            edge
            for edge in self.edges
            if edge.source == node_id
            and (edge.condition is None or edge.condition.matches(result))
        ]

    def validate(self) -> None:
        """Valida el grafo; lanza GraphValidationError con TODOS los problemas."""
        problems: list[str] = []
        if not self.nodes:
            problems.append("ExecutionGraph: sin nodos")
        if not isinstance(self.entry_node, str) or not self.entry_node:
            problems.append("ExecutionGraph: entry_node vacío")
        elif self.entry_node not in self.nodes:
            problems.append(
                f"ExecutionGraph: entry_node '{self.entry_node}' no existe"
            )

        seen_ids: set[str] = set()
        for node in self.nodes.values():
            if not isinstance(node, Node):
                problems.append(
                    f"ExecutionGraph: valor de nodo no es Node: {type(node).__name__}"
                )
                continue
            if node.id in seen_ids:
                problems.append(f"ExecutionGraph: ids de nodos duplicados: '{node.id}'")
            seen_ids.add(node.id)

        for edge in self.edges:
            if not isinstance(edge, Edge):
                problems.append(
                    f"ExecutionGraph: arista no es Edge: {type(edge).__name__}"
                )
                continue
            if edge.source not in self.nodes:
                problems.append(
                    f"ExecutionGraph: edge '{edge.id}' source '{edge.source}' no existe"
                )
            if edge.target not in self.nodes:
                problems.append(
                    f"ExecutionGraph: edge '{edge.id}' target '{edge.target}' no existe"
                )
            if edge.payload not in _VALID_PAYLOADS:
                problems.append(
                    f"ExecutionGraph: edge '{edge.id}' payload inválido '{edge.payload}'"
                )

        if self.nodes and self.entry_node in self.nodes:
            reachable = self._reachable_from(self.entry_node)
            for node_id in self.nodes:
                if node_id not in reachable:
                    problems.append(
                        f"ExecutionGraph: nodo '{node_id}' inalcanzable desde entry "
                        f"'{self.entry_node}'"
                    )

        if problems:
            raise GraphValidationError("\n".join(problems))

    def has_cycles(self) -> bool:
        """Detección DFS de ciclos (los ciclos son legales; se acotan por límites)."""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node_id: str) -> bool:
            if node_id in rec_stack:
                return True
            if node_id in visited:
                return False
            visited.add(node_id)
            rec_stack.add(node_id)
            for edge in self.edges:
                if edge.source == node_id and edge.target in self.nodes:
                    if dfs(edge.target):
                        return True
            rec_stack.discard(node_id)
            return False

        for node_id in self.nodes:
            if node_id not in visited:
                if dfs(node_id):
                    return True
        return False

    def _reachable_from(self, start: str) -> set[str]:
        reached: set[str] = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for edge in self.edges:
                if edge.source == current and edge.target not in reached:
                    reached.add(edge.target)
                    frontier.append(edge.target)
        return reached

    def to_dict(self) -> dict:
        return {
            "entry_node": self.entry_node,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges],
            "limits": self.limits.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionGraph":
        nodes = {nid: Node.from_dict(nd) for nid, nd in d.get("nodes", {}).items()}
        edges = [Edge.from_dict(e) for e in d.get("edges", [])]
        return cls(
            entry_node=d["entry_node"],
            nodes=nodes,
            edges=edges,
            limits=GraphLimits.from_dict(d.get("limits", {})),
        )


def _edge_condition_from_dict(d: dict) -> EdgeCondition:
    return EdgeCondition.from_dict(d)
