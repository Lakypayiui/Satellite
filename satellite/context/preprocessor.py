"""LocalPreprocessor: refinado del goal + qué contexto falta + preguntas.

Un LLM (el proveedor del rol de contexto) recibe el goal primero y emite
decisiones ESTRUCTURADAS (no ejecuta nada): refined_prompt, files/symbols
necesarios, y si necesita que el usuario aclare algo. Se activa únicamente
cuando `use_local_llm` está habilitado (checkbox en Ajustes).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from satellite.core.language import OUTPUT_LANGUAGE_RULE
from satellite.core.messages import LLMRequest, Message, Role
from satellite.providers.base import LLMProvider
from satellite.providers.parsing import extract_json_object


class LocalPreprocessor:
    """Refina el goal del usuario con el LLM del rol de contexto."""

    def __init__(
        self,
        provider: LLMProvider,
        project_root: Path,
        max_rounds: int = 3,
    ) -> None:
        self.provider = provider
        self.project_root = Path(project_root)
        self.max_rounds = max_rounds

    async def preprocess(self, user_goal: str) -> dict[str, Any]:
        """Devuelve la decisión estructurada del preprocesador.

        - refined_prompt: goal reescrito/compactado.
        - needs_user_input: True si el modelo cree que falta info del usuario.
        - user_prompt: pregunta de aclaración.
        - missing_info: categorías de contexto faltante.
        - files_needed / symbols_needed: archivos/símbolos que el runtime
          debe traer del índice.
        """
        context_so_far = ""
        for round_i in range(self.max_rounds):
            result = await self._analyze(user_goal, context_so_far)
            needs = result.get("needs") or {}
            files = needs.get("files_needed", [])
            symbols = needs.get("symbols_needed", [])
            # Si no pide más contexto ni aclaración → listo
            if not files and not symbols and not result.get("missing_info"):
                break
            fetched = self._gather(files, symbols)
            context_so_far = fetched
        return {
            "refined_prompt": result.get("refined_prompt", user_goal),
            "needs_user_input": bool(result.get("needs_user_input", False)),
            "user_prompt": result.get("user_prompt", ""),
            "missing_info": result.get("missing_info", []),
            "files_needed": files,
            "symbols_needed": symbols,
            "context": context_so_far,
        }

    async def _analyze(self, goal: str, context_so_far: str) -> dict[str, Any]:
        """Una ronda: el LLM decide qué falta y qué preguntar."""
        prompt = (
            "You are the Satellite context preprocessor. Given a user goal, decide "
            "WHAT information is missing in order to execute it.\n\n"
            "User goal (input data; keep this block as given and do not rewrite it):\n"
            f"{goal}\n\n"
            "Project context already available (if any):\n"
            f"{context_so_far or '(none)'}\n\n"
            "Reply ONLY with JSON with this shape:\n"
            "{\n"
            '  "refined_prompt": "the working goal, ALWAYS in English: a concise, faithful restatement of the user\'s intent (translate the user goal into English when it is written in another language)",\n'
            '  "needs_user_input": true|false,\n'
            '  "user_prompt": "clarifying question for the user, in the user\'s language (only used when needs_user_input)",\n'
            '  "missing_info": ["category of missing context, in English (internal)"],\n'
            '  "needs": {"files_needed": ["relative path"], "symbols_needed": ["symbol"]}\n'
            "}\n"
            "`refined_prompt` and `missing_info` are INTERNAL and ALWAYS in English: "
            "even when the goal is already clear, still return a concise English "
            "`refined_prompt` that faithfully restates it — never echo a non-English "
            "goal verbatim and never leave `refined_prompt` in the user's language. "
            "Only `user_prompt` is read by the user and follows the user's language."
        )
        request = LLMRequest(
            messages=(
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "You are the Satellite context preprocessor. You only emit "
                        "decisions, you do not execute.\n\n" + OUTPUT_LANGUAGE_RULE
                    ),
                ),
                Message(role=Role.USER, content=prompt),
            ),
            temperature=0.0,
            max_tokens=600,
        )
        response = await self.provider.complete(request)
        data = extract_json_object(response.content or "") or {}
        return data

    def _gather(self, files: list[str], symbols: list[str]) -> str:
        """Trae contenido real del proyecto (simple) para la siguiente ronda."""
        parts: list[str] = []
        for rel in files[:20]:
            path = self.project_root / rel
            if path.is_file():
                try:
                    parts.append(f"--- {rel} ---\n" + path.read_text(encoding="utf-8", errors="replace")[:4000])
                except OSError:
                    continue
        for sym in symbols[:20]:
            parts.append(f"[requested symbol: {sym}]")
        return "\n\n".join(parts)
