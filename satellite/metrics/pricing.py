"""Precios por modelo (configurables; sin precios hardcodeados).

Unidades: **USD por millón de tokens**. Si no hay precios configurados, los
costes son ``None`` (el informe muestra "unknown" en vez de inventar cifras).
"""

from __future__ import annotations

from dataclasses import dataclass

PER_MILLION = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    """Precio de un modelo: entrada, salida y cache (escritura/lectura)."""

    input: float = 0.0
    output: float = 0.0
    cache_write: float = 0.0
    cache_read: float = 0.0

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelPrice":
        return cls(
            input=float(d.get("input", 0.0)),
            output=float(d.get("output", 0.0)),
            cache_write=float(d.get("cache_write", d.get("cache_creation", 0.0))),
            cache_read=float(d.get("cache_read", d.get("cache_hit", 0.0))),
        )


class PriceBook:
    """Precios por modelo (coincidencia exacta o por prefijo)."""

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self.prices = {str(model): price for model, price in (prices or {}).items()}

    def __len__(self) -> int:
        return len(self.prices)

    @property
    def is_empty(self) -> bool:
        """True si no hay ningún precio configurado."""
        return not self.prices

    @classmethod
    def from_config(cls, data: dict | None) -> "PriceBook":
        """Construye el libro desde ``config.json`` (``llm.pricing`` o ``pricing``)."""
        if not isinstance(data, dict):
            return cls()
        raw = data.get("pricing")
        llm = data.get("llm")
        if raw is None and isinstance(llm, dict):
            raw = llm.get("pricing")
        if not isinstance(raw, dict):
            return cls()
        prices: dict[str, ModelPrice] = {}
        for model, value in raw.items():
            if isinstance(value, dict):
                prices[str(model)] = ModelPrice.from_dict(value)
        return cls(prices)

    def price_for(self, model: str) -> ModelPrice | None:
        """Precio del modelo (exacto, o el prefijo más largo que coincida)."""
        if not self.prices or not model:
            return None
        if model in self.prices:
            return self.prices[model]
        candidates = [
            (name, price) for name, price in self.prices.items() if model.startswith(name)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: len(item[0]))[1]

    def cost(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_creation: int = 0,
        cache_read: int = 0,
    ) -> float | None:
        """Coste real de una llamada (con cache); ``None`` si no hay precio."""
        price = self.price_for(model)
        if price is None:
            return None
        # `input_tokens` es el TOTAL (incluye lo leído y lo escrito en cache).
        plain = max(0, int(input_tokens) - int(cache_creation) - int(cache_read))
        total = (
            plain * price.input
            + int(cache_creation) * price.cache_write
            + int(cache_read) * price.cache_read
            + int(output_tokens) * price.output
        )
        return round(total / PER_MILLION, 8)

    def hypothetical_cost(
        self, model: str, *, input_tokens: int, output_tokens: int
    ) -> float | None:
        """Coste hipotético **sin cache** (todo el input a precio pleno)."""
        price = self.price_for(model)
        if price is None:
            return None
        total = int(input_tokens) * price.input + int(output_tokens) * price.output
        return round(total / PER_MILLION, 8)


def price_book_from_config(data: dict | None) -> PriceBook:
    """Atajo: libro de precios desde ``config.json``."""
    return PriceBook.from_config(data)
