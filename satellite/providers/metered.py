"""Proveedor medido: envuelve un ``LLMProvider`` y registra cada llamada.

Se instala **sólo** en ``runtime_builder``: ni los agentes ni el orquestador
saben nada de métricas. Registra hashes y contadores (nunca contenido), calcula
el estado de cache, clasifica la deriva del prefijo y aplica
``llm.max_tokens.<rol>`` cuando la petición no trae límite propio.
"""

from __future__ import annotations

from dataclasses import replace

from satellite.core.messages import CallContext, LLMRequest, LLMResponse, Message, Role
from satellite.metrics.pricing import PriceBook
from satellite.observability.call_log import CallRecord, CallRecorder, cache_status
from satellite.observability.drift import PrefixDriftDetector, diagnosis_kind
from satellite.orchestration.prefix_rules import PrefixGate, PrefixGroupTracker
from satellite.providers.base import LLMProvider
from satellite.providers.prefix import (
    estimate_prefix_tokens,
    fingerprint,
    prefix_payload,
    tools_fingerprint,
)

# Mensaje de relleno del pre-calentado: su contenido no se responde nunca.
PREWARM_PLACEHOLDER = "warmup"


class MeteredProvider(LLMProvider):
    """Decorador de provider: mide, registra y no cambia el comportamiento."""

    def __init__(
        self,
        inner: LLMProvider,
        recorder: CallRecorder | None = None,
        *,
        provider_name: str = "",
        model: str = "",
        price_book: PriceBook | None = None,
        detector: PrefixDriftDetector | None = None,
        max_tokens_by_role: dict | None = None,
        gate: PrefixGate | None = None,
        tracker: PrefixGroupTracker | None = None,
        prewarm: bool = False,
    ) -> None:
        self.inner = inner
        self.recorder = recorder
        self.provider_name = provider_name or getattr(inner, "name", "") or type(inner).__name__
        self.model = model
        self.price_book = price_book
        self.detector = detector or PrefixDriftDetector()
        self.max_tokens_by_role = dict(max_tokens_by_role or {})
        # Reglas de ejecución (F5): calentamiento del prefijo y vigilancia del grupo.
        self.gate = gate if gate is not None else PrefixGate()
        self.tracker = tracker if tracker is not None else PrefixGroupTracker()
        # Pre-calentado de caché (`llm.cache.prewarm`): opt-in y best-effort.
        self.prewarm = bool(prewarm)
        self.last_prewarmed = False
        self.last_prewarm_creation: int | None = None
        self.last_record: CallRecord | None = None
        self.last_warmed = False
        self.last_group_status = ""

    # --- delegación de capacidades ---

    @property
    def reports_usage(self) -> bool:
        return bool(getattr(self.inner, "reports_usage", True))

    @property
    def automatic_cache(self) -> bool:
        return bool(getattr(self.inner, "automatic_cache", False))

    def cache_strategy(self):
        return self.inner.cache_strategy()

    # --- llamada ---

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Completa la petición midiéndola (el error del inner se propaga igual).

        La primera llamada de un prefijo se hace en solitario (calentamiento) y
        el resto espera a que termine para salir en paralelo.
        """
        request = self._with_max_tokens(request)
        payload, payload_hash = self._prefix_parts(request)
        try:
            async with self.gate.enter(payload_hash) as warmed:
                self.last_warmed = warmed
                if warmed and self.prewarm:
                    await self._prewarm(request)
                response = await self.inner.complete(request)
        finally:
            self.last_group_status = self._observe_group(request, payload_hash)
        if self.recorder is not None:
            self.last_record = self._build_record(request, response, payload, payload_hash)
            self.recorder.record(self.last_record)
        return response

    async def _prewarm(self, request: LLMRequest) -> None:
        """Calienta el prefijo estable con ``max_tokens=0`` (best-effort).

        Se mandan los **mismos** tools y system que la llamada real, con el
        breakpoint en el catálogo y sin la cola dinámica. Una respuesta con
        contenido vacío y ``stop_reason: max_tokens`` es el resultado esperado (no
        un fallo) y su ``cache_creation`` se reporta en ``last_prewarm_creation``.
        """
        keep = min(max(1, int(request.stable_user_count)), len(request.messages))
        messages = tuple(request.messages[:keep]) + (
            Message(role=Role.USER, content=PREWARM_PLACEHOLDER),
        )
        warm_request = replace(
            request,
            messages=messages,
            max_tokens=0,
            # El único bloque dinámico es el relleno: así el breakpoint cae en el
            # catálogo (lo que se quiere calentar) y no en el placeholder.
            stable_user_count=len(messages) - 1,
        )
        self.last_prewarmed = False
        self.last_prewarm_creation = None
        try:
            response = await self.inner.complete(warm_request)
        except Exception:  # noqa: BLE001 — el calentamiento nunca rompe la llamada
            return
        self.last_prewarmed = True
        self.last_prewarm_creation = (
            response.usage.cache_creation_input_tokens if response.usage else None
        )

    # --- construcción del registro ---

    def _with_max_tokens(self, request: LLMRequest) -> LLMRequest:
        """Aplica ``llm.max_tokens.<rol>`` sólo si la petición no trae límite."""
        if request.max_tokens is not None or not self.max_tokens_by_role:
            return request
        role = request.call.role if request.call else ""
        limit = self.max_tokens_by_role.get(role) or self.max_tokens_by_role.get("*")
        return replace(request, max_tokens=limit) if limit else request

    def _prefix_parts(self, request: LLMRequest) -> tuple[str, str]:
        """``(payload canónico, sha256)`` del prefijo estable de la petición."""
        system_text = next(
            (
                message.content
                for message in request.messages
                if message.role is Role.SYSTEM
            ),
            "",
        )
        catalog_text = (
            request.messages[1].content
            if request.stable_user_count >= 1 and len(request.messages) > 1
            else ""
        )
        payload = prefix_payload(request.tools, system_text, catalog_text)
        return payload, fingerprint(payload)

    def _observe_group(self, request: LLMRequest, payload_hash: str) -> str:
        """Registra la llamada en el tracker y devuelve si el grupo sigue estable."""
        call = request.call or CallContext()
        return self.tracker.observe(
            task_id=call.task_id,
            spec_id=call.agent_id or call.role,
            prefix_hash=payload_hash,
            model=self.model,
        )

    def _build_record(
        self,
        request: LLMRequest,
        response: LLMResponse,
        payload: str,
        payload_hash: str,
    ) -> CallRecord:
        call = request.call or CallContext()
        tools_fp = tools_fingerprint(request.tools)
        usage = response.usage
        creation = usage.cache_creation_input_tokens if usage else None
        read = usage.cache_read_input_tokens if usage else None
        plan = getattr(self.inner, "last_plan", None)
        ttl = str(getattr(plan, "ttl", "") or "") if plan is not None else ""
        # Los breakpoints son **segmentos** ("system"/"catalog"/"anchor"/"tail").
        breakpoints = tuple(str(b) for b in (getattr(plan, "breakpoints", ()) or ()))
        kind = self.detector.classify(
            task_id=call.task_id,
            agent_id=call.agent_id or call.role,
            model=self.model,
            prefix_hash=payload_hash,
            spec_fp=call.spec_fp,
            catalog_fp=call.catalog_fp,
            tools_fp=tools_fp,
        )
        reason = response.cache_diagnostics or {}
        cache_miss = (reason.get("cache_miss_reason") or {}) if isinstance(reason, dict) else {}
        return CallRecord(
            task_id=call.task_id,
            node_id=call.node_id,
            agent_id=call.agent_id,
            role=call.role,
            model=self.model,
            provider=self.provider_name,
            ttl=ttl,
            prefix_hash=payload_hash,
            prefix_tokens_estimate=estimate_prefix_tokens(payload),
            cache_status=cache_status(creation, read),
            breakpoints=breakpoints,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cache_creation=creation,
            cache_read=read,
            spec_fp=call.spec_fp,
            catalog_fp=call.catalog_fp,
            tools_fp=tools_fp,
            kind=kind,
            message_id=response.response_id,
            cache_miss_reason=str(cache_miss.get("type", "")),
            diagnosis=diagnosis_kind(response.cache_diagnostics),
        )
