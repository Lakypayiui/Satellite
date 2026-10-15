"""Schemas JSON de las salidas de los agentes base (datos puros, sin imports)."""

from __future__ import annotations

PLAN_STEP_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "agent_id": {"type": "string"},
        "instruction": {"type": "string"},
        "depends_on": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "agent_id", "instruction"],
}

ORCHESTRATOR_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["plan", "capability_gap", "needs_clarification"],
        },
        "steps": {"type": "array", "items": PLAN_STEP_SCHEMA},
        "rationale": {"type": "string"},
        "missing": {"type": "string"},
        "steps_blocked": {"type": "array", "items": {"type": "string"}},
        "partial_plan": {"type": "array", "items": PLAN_STEP_SCHEMA},
        "question": {"type": "string"},
    },
}

PROPOSED_AGENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "proposed_agent",
                "extend",
                "skill_suggestion",
                "reuse",
                "needs_clarification",
            ],
        },
        "id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "capabilities": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "string"}},
        "tools": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "validators": {"type": "array", "items": {"type": "string"}},
        "model_role": {"type": "string"},
        "max_turns": {"type": "integer"},
        "timeout_s": {"type": "integer"},
        "prompt_template": {"type": "string"},
        "system_prompt": {
            "type": "string",
            "description": (
                "Parte ESTÁTICA del prompt. No puede contener {task}, {context} "
                "ni {feedback}. Sí puede usar {tools} y {output_schema}."
            ),
        },
        "user_template": {
            "type": "string",
            "description": "Parte dinámica: {task}, {context}, {feedback}.",
        },
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "can_call": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        },
        "skills_base": {"type": "array", "items": {"type": "string"}},
        "example_tasks": {
            "type": "array",
            "minItems": 3,
            "items": {"type": "string", "description": "Tarea distinta que atendería el rol."},
        },
        "rationale": {"type": "string"},
        "reuse_checked": {"type": "boolean"},
        "reuse_candidate": {"type": "string"},
        "add_capabilities": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string", "enum": ["python", "bash"]},
        "entrypoint": {"type": "string"},
        "script": {"type": "string"},
        "inputs": {"type": "object"},
        "outputs": {"type": "object"},
        "permissions": {"type": "object"},
        "deterministic": {
            "type": "boolean",
            "description": (
                "true solo si la skill es una transformación pura (sin red, sin "
                "escritura, sin hora ni entorno): entonces su resultado se puede "
                "memoizar. Declarativo: no se analiza el código."
            ),
        },
        "examples": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "agent_id": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["type"],
}

PLAN_OPTIONS_SCHEMA: dict = {
    "type": "object",
    "required": ["question", "options"],
    "properties": {
        "question": {"type": "string"},
        "recommendation": {"type": "string"},
        "options": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["id", "title", "summary"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "approach": {"type": "array", "items": {"type": "string"}},
                    "tradeoffs": {"type": "string"},
                    "effort": {"type": "string"},
                    "risk": {"type": "string"},
                },
            },
        },
    },
}

CURATOR_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "merge",
                            "edit",
                            "deprecate",
                            "delete",
                            "retag",
                            "flag_suspicious",
                        ],
                    },
                    "targets": {"type": "array", "items": {"type": "string"}},
                    "proposal": {"type": "object"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "object"},
                },
                "required": ["op", "targets", "reason"],
            },
        }
    },
    "required": ["operations"],
}
