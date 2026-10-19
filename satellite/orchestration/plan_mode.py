"""Modo plan: alternativas de enfoque desarrolladas por el LLM.

Se activa cuando el usuario lo pide (``plan_mode``) o cuando el orquestador
devuelve ``needs_clarification``. El usuario elige una alternativa (o describe
la suya) y la ejecución continúa con esa decisión.
"""

from __future__ import annotations

from satellite.agents.prompting import build_meta_messages
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.schemas import PLAN_OPTIONS_SCHEMA
from satellite.core.errors import ContractError, SpecValidationError
from satellite.core.messages import CallContext, LLMRequest
from satellite.core.plan import PlanOptions
from satellite.providers.base import LLMProvider
from satellite.providers.parsing import extract_json_object
from satellite.providers.prefix import fingerprint

DEFAULT_PROMPT_ID = "plan_options"


class PlanOptionGenerator:
    """Pide al LLM varias alternativas y las valida como ``PlanOptions``."""

    def __init__(
        self,
        provider: LLMProvider,
        prompts: DefaultPromptRegistry,
        *,
        prompt_id: str = DEFAULT_PROMPT_ID,
        max_attempts: int = 2,
        min_options: int = 2,
        max_options: int = 4,
    ) -> None:
        self.provider = provider
        self.prompts = prompts
        self.prompt_id = prompt_id
        self.max_attempts = max_attempts
        self.min_options = min_options
        self.max_options = max_options

    async def generate(
        self,
        task: str,
        *,
        context: str = "",
        history: str = "",
        feedback: str = "",
    ) -> PlanOptions | None:
        """Devuelve alternativas válidas, o ``None`` si el LLM no colabora.

        ``context`` es el bloque estable (catálogo); ``history`` es dinámico.
        """
        system_prompt = self.prompts.get_system(self.prompt_id)
        user_template = self.prompts.get_user(self.prompt_id)
        errors = feedback

        for _ in range(self.max_attempts):
            messages, stable = build_meta_messages(
                system_prompt=system_prompt,
                user_template=user_template,
                output_schema=PLAN_OPTIONS_SCHEMA,
                catalog=context,
                context=history,
                task=task,
                feedback=errors,
            )
            response = await self.provider.complete(
                LLMRequest(
                    messages=messages,
                    temperature=0.0,
                    stable_user_count=stable,
                    call=CallContext(role="plan", catalog_fp=fingerprint(context)),
                )
            )
            data = extract_json_object(response.content or "")
            if data is None:
                errors = "previous response was not valid JSON"
                continue
            try:
                options = PlanOptions.from_dict(data)
                self._check_size(options)
            except (SpecValidationError, ContractError) as exc:
                errors = f"your response does not match the schema: {exc}"
                continue
            return options
        return None

    def _check_size(self, options: PlanOptions) -> None:
        if len(options.options) < self.min_options:
            raise SpecValidationError(
                f"expected at least {self.min_options} alternatives, "
                f"received {len(options.options)}"
            )
        if len(options.options) > self.max_options:
            raise SpecValidationError(
                f"expected at most {self.max_options} alternatives, "
                f"received {len(options.options)}"
            )
