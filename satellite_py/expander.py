"""Python-side agent expansion.

Genera la spec de un microagente con el **proveedor LLM configurado** (el
mismo que usa el resto del runtime: local/openai/anthropic/deepseek vía
``llm.py``) y compila/testea/registra a través de la factory C++ en modo
``spec`` (la factory ya no genera con DeepSeek por defecto).
"""

from __future__ import annotations

import json
import os
from typing import Any

from .llm import LLMClient
from .registry import AgentDescriptor, AgentRegistry
from .runtime.factory_bridge import create_agent_from_spec

_SYSTEM_PROMPT = (
    "Eres el generador de microagentes C++ de Satellite. Dada una capacidad "
    "faltante, escribe SOLO las asignaciones de la lógica de cálculo en C++ "
    "(sin includes, sin clases, sin declarar json in/out): las variables "
    "'json in' (entrada) y 'json out' (resultado) YA existen. Ejemplo de "
    "lógica: out[\"result\"] = in[\"a\"].get<double>() + 1; "
    'Responde SOLO con JSON: {"name": "...", "description": "...", '
    '"input_schema": {...}, "output_schema": {...}, "implementation_logic": '
    '"...", "test_cases": [{"input": {...}, "expected": {...}}]}. '
    "El JSON de implementation_logic debe tener los saltos de línea como \\n "
    "escapados. input_schema/output_schema son JSON Schema válidos."
)

_SYSTEM_PROMPT_EFFECTS = (
    "Eres el generador de microagentes C++ de Satellite con EFECTOS de "
    "sistema. Dada una capacidad faltante, escribe SOLO las asignaciones de la "
    "lógica en C++ (sin includes, sin clases, sin declarar json in/out; las "
    "variables 'json in' y 'json out' YA existen, y las variables 'sb' y "
    "'out' referencian el sandbox). Tienes disponibles: "
    "sb.write_file(rel, content) para crear/editar archivos, "
    "sb.read_file(rel) para leerlos, sb.run_process(cmd, cwd, timeout_ms) para "
    "ejecutar comandos, sb.http_get(url) para requests GET. Los paths son "
    "SIEMPRE relativos al proyecto. NUNCA toques nada dentro de '.satellite/'. "
    "Puedes encadenar efectos: escribir un archivo y luego ejecutarlo. "
    'Responde SOLO con JSON: {"name": "...", "description": "...", '
    '"input_schema": {...}, "output_schema": {...}, "implementation_logic": '
    '"...", "test_cases": [{"input": {...}, "expected": {...}}]}. '
    "El JSON de implementation_logic debe tener los saltos de línea como \\n "
    "escapados."
)

_ABI_TEMPLATE = '''#include "core/agent/IAgent.h"
#include <json.hpp>

using json = nlohmann::json;
namespace a = satellite::core::agent;

class {CLASS} : public a::IAgent {{
public:
  a::AgentResult execute(const a::AgentRequest& req) override {{
    a::AgentResult result;
    result.agent_id = req.agent_id;
    try {{
      json in = req.input;
      json out;
      {LOGIC}
      result.output = out;
      result.status = a::AgentStatus::SUCCESS;
    }} catch (const std::exception& error) {{
      result.status = a::AgentStatus::FAILED;
      a::AgentError agent_error;
      agent_error.message = error.what();
      result.error = agent_error;
    }}
    return result;
  }}
}};

extern "C" a::IAgent* satellite_create_agent() {{ return new {CLASS}(); }}
extern "C" void satellite_destroy_agent(a::IAgent* instance) {{ delete instance; }}
'''


# Plantilla para agentes con EFECTOS de sistema (escritura/proceso/red).
# Expone las helpers del sandbox (satellite::core::agent::sandbox_write_file,
# sandbox_run_process, …) y acota las escrituras al proyecto (work_dir)
# rechazando .satellite/. El LLM genera la lógica que llama a estas funciones
# en {LOGIC} — la variable `sb` ya existe y da acceso a los efectos.
_ABI_SANDBOX_TEMPLATE = '''#include "core/agent/IAgent.h"
#include "core/agent/AgentSandbox.h"
#include <json.hpp>
#include <string>

using json = nlohmann::json;
namespace a = satellite::core::agent;

class {CLASS} : public a::IAgent {{
public:
  a::AgentResult execute(const a::AgentRequest& req) override {{
    a::AgentResult result;
    result.agent_id = req.agent_id;
    if (!req.sandbox) {{
      result.status = a::AgentStatus::FAILED;
      a::AgentError err; err.message = "el runtime no habilito efectos de sistema";
      result.error = err;
      return result;
    }}
    try {{
      json in = req.input;
      json out;
      const a::AgentSandbox& sb = *req.sandbox;
      {LOGIC}
      result.output = out;
      result.status = a::AgentStatus::SUCCESS;
    }} catch (const std::exception& error) {{
      result.status = a::AgentStatus::FAILED;
      a::AgentError err; err.message = error.what();
      result.error = err;
    }}
    return result;
  }}
}};

extern "C" a::IAgent* satellite_create_agent() {{ return new {CLASS}(); }}
extern "C" void satellite_destroy_agent(a::IAgent* instance) {{ delete instance; }}
'''


# Capacidades que implican efectos de sistema (no cómputo puro).
_EFFECT_CAPABILITIES = {
    "filesystem.write", "filesystem.read", "process.execute",
    "compiler.execute", "network.request",
}

class AgentExpander:
    """Request missing capabilities and register returned descriptors."""

    def __init__(
        self,
        registry: AgentRegistry,
        client: LLMClient | None = None,
        cwd: str | os.PathLike | None = None,
    ) -> None:
        self.registry = registry
        self.client = client
        self.cwd = cwd

    @staticmethod
    def build_prompt(goal: str, capability: str, use_effects: bool = False) -> str:
        if use_effects:
            return (
                "Genera la especificación de un microagente C++ para Satellite "
                "con EFECTOS de sistema. "
                f"Objetivo: {goal}. Capacidad requerida: {capability}. "
                "Puede crear/editar archivos (sb.write_file), leerlos "
                "(sb.read_file), ejecutar comandos (sb.run_process) y hacer "
                "requests GET (sb.http_get). Los paths son relativos al "
                "proyecto y NO puede tocar '.satellite/'. Escribe SOLO las "
                "asignaciones de la lógica en C++ (sin includes/clases/"
                "declaraciones): 'json in' y 'json out' ya existen, y 'sb' "
                "referencia el sandbox; asigna out[...] con los resultados. "
                'Responde SOLO con JSON: {"name": "...", "description": "...", '
                '"input_schema": {...}, "output_schema": {...}, '
                '"implementation_logic": "...", "test_cases": '
                '[{"input": {...}, "expected": {...}}]}. '
                "El JSON de implementation_logic debe tener los saltos de línea "
                "como \\n escapados."
            )
        return (
            "Genera la especificación de un microagente C++ para Satellite. "
            f"Objetivo: {goal}. Capacidad requerida: {capability}. "
            "Escribe SOLO las asignaciones de la lógica en C++ (sin "
            "includes/clases/declaraciones): 'json in' (entrada) y 'json out' "
            "(resultado) ya existen; asigna out[...] leyendo de in[...]. "
            'Responde SOLO con JSON: {"name": "...", "description": "...", '
            '"input_schema": {...}, "output_schema": {...}, '
            '"implementation_logic": "...", "test_cases": '
            '[{"input": {...}, "expected": {...}}]}. '
            "El JSON de implementation_logic debe tener los saltos de línea "
            "como \\n escapados."
        )

    def generate_spec(self, goal: str, capability: str) -> dict[str, Any]:
        """Pide la spec (lógica) al LLM configurado y la devuelve como dict."""
        client = self.client
        if client is None:
            from .llm import load_llm_config

            client = load_llm_config().create_client()
        use_effects = capability in _EFFECT_CAPABILITIES
        system = _SYSTEM_PROMPT_EFFECTS if use_effects else _SYSTEM_PROMPT
        text = client.complete(
            system,
            self.build_prompt(goal, capability, use_effects=use_effects),
            max_tokens=2500,
        )
        payload = _extract_spec_json(text)
        if not payload:
            raise RuntimeError("el LLM no devolvió una spec JSON válida")
        for required in ("name", "input_schema", "output_schema", "implementation_logic", "test_cases"):
            if required not in payload:
                raise RuntimeError(f"spec incompleta: falta '{required}'")
        payload.setdefault("description", "")
        payload.setdefault("version", "1.0.0")
        return payload

    @staticmethod
    def build_implementation_code(spec: dict[str, Any], use_effects: bool = False) -> str:
        """Envuelve la lógica generada en la plantilla ABI (compila siempre).

        La lógica generada por el LLM debe declarar ``json in = req.input;``
        y dejar el resultado en ``json out;`` (sin return). Si trae return,
        se convierte a asignación sobre ``out`` para que el template funcione.
        Con ``use_effects`` se usa la plantilla sandbox (el agente puede
        escribir/leer en el workspace del proyecto).
        """
        name = spec.get("name") or "GeneratedAgent"
        class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_")) or "GeneratedAgent"
        if not class_name[0].isalpha():
            class_name = "Agent" + class_name
        logic = spec.get("implementation_logic", "").strip()
        # Normalizar: quitar un `return out;` final (el template asigna out) y
        # las declaraciones de in/out que el modelo repita (el template ya las
        # declara).
        import re as _re

        logic = _re.sub(r"\breturn\s+out\s*;", "", logic)
        logic = _re.sub(r"\bjson\s+in\s*=\s*req\.input\s*;", "", logic)
        logic = _re.sub(r"\bjson\s+out\s*;", "", logic)
        template = _ABI_SANDBOX_TEMPLATE if use_effects else _ABI_TEMPLATE
        return template.format(CLASS=class_name, LOGIC=logic)

    def expand(self, goal: str, capability: str) -> AgentDescriptor:
        """Genera la spec (provider configurado), compila vía factory y registra."""
        spec = self.generate_spec(goal, capability)
        use_effects = capability in _EFFECT_CAPABILITIES
        spec.setdefault("capabilities", [capability])
        spec["implementation_code"] = self.build_implementation_code(spec, use_effects=use_effects)
        # Id libre: 1-5 nativos; el siguiente no usado en el registry actual.
        spec["id"] = self._next_id()
        response = create_agent_from_spec(spec, cwd=self.cwd)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "agent expansion failed"))

        payload = response.get("descriptor")
        if not isinstance(payload, dict):
            raise RuntimeError("factory response has no descriptor")

        descriptor = AgentDescriptor(
            id=payload["id"],
            name=payload.get("name", spec.get("name", "")),
            description=payload.get("description", ""),
            version=payload.get("version", "0.1.0"),
            input_schema=payload.get("input_schema", {}),
            output_schema=payload.get("output_schema", {}),
            context_requirements=payload.get("context_requirements", []),
            capabilities=payload.get("capabilities", [capability]),
            library_path=payload["library_path"],
        )
        if not self.registry.register_agent(descriptor):
            raise RuntimeError(f"agent id already registered: {descriptor.id}")
        return descriptor

    def _next_id(self) -> int:
        known = {a.id for a in self.registry.list_agents()}
        candidate = 6
        while candidate in known:
            candidate += 1
        return candidate


def _extract_spec_json(text: str) -> dict[str, Any] | None:
    """Return the first balanced JSON object in ``text`` (robust to prose)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif in_string and char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string and char == "{":
            depth += 1
        elif not in_string and char == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(text[start : index + 1])
                    return payload if isinstance(payload, dict) else None
                except ValueError:
                    return None
    return None
