"""Informe de cache agregado a partir del registro por llamada.

Cuando un dato no está reportado por el proveedor se muestra **"unknown"** (nunca
"0"), y cuando no hay precios configurados los costes son ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from satellite.metrics.pricing import PriceBook
from satellite.observability.call_log import (
    CACHED,
    KIND_DRIFT,
    NOT_CACHED,
    UNKNOWN,
    WRITTEN,
    CallRecord,
    cache_status,
)

UNKNOWN_TEXT = "unknown"


def _fmt_percent(value: float | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value * 100:.1f}%"


def _fmt_cost(value: float | None, currency: str = "USD") -> str:
    return UNKNOWN_TEXT if value is None else f"{value:.6f} {currency}"


def _fmt_int(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else str(value)


@dataclass(frozen=True)
class CacheReport:
    """Agregado de llamadas: tokens, aciertos de cache, coste real vs hipotético."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    prefix_tokens_estimate: int = 0
    cache_creation: int | None = None
    cache_read: int | None = None
    hit_rate: float | None = None
    real_cost: float | None = None
    hypothetical_cost: float | None = None
    savings: float | None = None
    statuses: dict = field(default_factory=dict)
    by_role: dict = field(default_factory=dict)
    by_agent: dict = field(default_factory=dict)
    drift_events: int = 0
    unknown_calls: int = 0
    priced_calls: int = 0
    currency: str = "USD"

    @property
    def savings_pct(self) -> float | None:
        """Ahorro relativo al coste hipotético sin cache."""
        if self.savings is None or not self.hypothetical_cost:
            return None
        return round(self.savings / self.hypothetical_cost, 4)

    @property
    def real_tokens_per_call(self) -> float | None:
        """Tokens efectivos por llamada (input a precio pleno + salida)."""
        if not self.calls:
            return None
        effective = self.input_tokens - (self.cache_read or 0)
        return round(max(0, effective) / self.calls, 2)

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "prefix_tokens_estimate": self.prefix_tokens_estimate,
            "cache_creation": self.cache_creation,
            "cache_read": self.cache_read,
            "hit_rate": self.hit_rate,
            "real_cost": self.real_cost,
            "hypothetical_cost": self.hypothetical_cost,
            "savings": self.savings,
            "savings_pct": self.savings_pct,
            "statuses": dict(self.statuses),
            "by_role": {key: dict(value) for key, value in self.by_role.items()},
            "by_agent": {key: dict(value) for key, value in self.by_agent.items()},
            "drift_events": self.drift_events,
            "unknown_calls": self.unknown_calls,
            "priced_calls": self.priced_calls,
            "currency": self.currency,
        }

    def format(self) -> str:
        """Texto legible para CLI/TUI (marca "unknown" cuando falta el dato)."""
        lines = [
            f"Cache LLM: {self.calls} llamadas"
            + (f" ({self.unknown_calls} sin datos del proveedor)" if self.unknown_calls else ""),
            f"  tokens: input={self.input_tokens} output={self.output_tokens} "
            f"cache_read={_fmt_int(self.cache_read)} cache_creation={_fmt_int(self.cache_creation)}",
            f"  hit rate: {_fmt_percent(self.hit_rate)}"
            f"  ·  prefijo estimado (última llamada): {self.prefix_tokens_estimate} tokens",
        ]
        if self.real_cost is None:
            lines.append("  coste: unknown (sin precios configurados en llm.pricing)")
        else:
            lines.append(
                f"  coste real: {_fmt_cost(self.real_cost, self.currency)}"
                f"  ·  sin cache: {_fmt_cost(self.hypothetical_cost, self.currency)}"
                f"  ·  ahorro: {_fmt_cost(self.savings, self.currency)}"
                f" ({_fmt_percent(self.savings_pct)})"
            )
        if self.statuses:
            order = (CACHED, WRITTEN, NOT_CACHED, UNKNOWN)
            pairs = [f"{name}={self.statuses.get(name, 0)}" for name in order]
            extra = [f"{k}={v}" for k, v in self.statuses.items() if k not in order]
            lines.append("  estados: " + " ".join(pairs + extra))
        lines.append(f"  deriva (CACHE_PREFIX_DRIFT): {self.drift_events}")
        return "\n".join(lines)


def _empty_bucket() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0}


def _finish_bucket(bucket: dict) -> dict:
    calls = bucket["calls"]
    input_tokens = bucket["input_tokens"]
    bucket["hit_rate"] = round(bucket["cache_read"] / input_tokens, 4) if input_tokens else None
    bucket["avg_input_tokens"] = round(input_tokens / calls, 2) if calls else 0.0
    return bucket


def build_cache_report(
    records: Iterable[CallRecord],
    price_book: PriceBook | None = None,
    *,
    currency: str = "USD",
) -> CacheReport:
    """Agrega los registros de llamada (tolerante a datos incompletos)."""
    items = list(records)
    if not items:
        return CacheReport(currency=currency)

    input_tokens = sum(record.input_tokens for record in items)
    output_tokens = sum(record.output_tokens for record in items)
    statuses: dict[str, int] = {}
    by_role: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}

    reported = [
        record
        for record in items
        if record.cache_read is not None or record.cache_creation is not None
    ]
    cache_read = sum(record.cache_read or 0 for record in reported) if reported else None
    cache_creation = (
        sum(record.cache_creation or 0 for record in reported) if reported else None
    )
    hit_rate = (
        round((cache_read or 0) / input_tokens, 4) if reported and input_tokens else None
    )

    real_cost: float | None = None
    hypothetical: float | None = None
    priced = 0
    for record in items:
        # se recalcula desde los contadores: el campo guardado puede venir de un
        # registro antiguo o manipulado
        status = cache_status(record.cache_creation, record.cache_read)
        statuses[status] = statuses.get(status, 0) + 1
        for table, key in (
            (by_role, record.role or "(sin rol)"),
            (by_agent, record.agent_id or "(sin agente)"),
        ):
            bucket = table.setdefault(key, _empty_bucket())
            bucket["calls"] += 1
            bucket["input_tokens"] += record.input_tokens
            bucket["output_tokens"] += record.output_tokens
            bucket["cache_read"] += record.cache_read or 0
        if price_book is None or price_book.is_empty:
            continue
        cost = price_book.cost(
            record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_creation=record.cache_creation or 0,
            cache_read=record.cache_read or 0,
        )
        if cost is None:
            continue
        priced += 1
        real_cost = round((real_cost or 0.0) + cost, 8)
        theoretical = price_book.hypothetical_cost(
            record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
        )
        if theoretical is not None:
            hypothetical = round((hypothetical or 0.0) + theoretical, 8)

    savings = (
        round(hypothetical - real_cost, 8)
        if hypothetical is not None and real_cost is not None
        else None
    )
    return CacheReport(
        calls=len(items),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prefix_tokens_estimate=items[-1].prefix_tokens_estimate,
        cache_creation=cache_creation,
        cache_read=cache_read,
        hit_rate=hit_rate,
        real_cost=real_cost,
        hypothetical_cost=hypothetical,
        savings=savings,
        statuses=statuses,
        by_role={key: _finish_bucket(value) for key, value in by_role.items()},
        by_agent={key: _finish_bucket(value) for key, value in by_agent.items()},
        drift_events=sum(1 for record in items if record.kind == KIND_DRIFT),
        unknown_calls=len(items) - len(reported),
        priced_calls=priced,
        currency=currency,
    )


def load_cache_report(
    project_root, *, limit: int | None = None, price_book: PriceBook | None = None
) -> CacheReport:
    """Informe a partir del ``calls.jsonl`` del proyecto."""
    from satellite.config import providers as provider_config
    from satellite.observability.call_log import CallRecorder, default_log_dir

    recorder = CallRecorder(default_log_dir(project_root))
    book = price_book if price_book is not None else PriceBook.from_config(
        provider_config.load_config(project_root)
    )
    return build_cache_report(recorder.load(limit), book)
