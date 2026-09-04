"""Plan generation and topological validation for Satellite."""

from dataclasses import dataclass, field
import json
import os
from typing import Any


@dataclass
class PlanStep:
    agent_id: int
    input: dict[str, Any] = field(default_factory=dict)
    dependencies: list[int] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    order: int = 0
    description: str = ""


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    goal: str = ""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start >= 0:
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
                    text = text[start : index + 1]
                    break
    return json.loads(text)


class Planner:
    """Generate and validate plans using an injected Anthropic/OpenAI client."""

    def __init__(self, client: Any | None = None, provider: str | None = None) -> None:
        self.client = client
        self.provider = provider or os.getenv("SATELLITE_LLM_PROVIDER", "openai")

    @staticmethod
    def build_prompt(goal: str, catalog_prompt: str) -> str:
        return (
            "Eres el planificador de Satellite. Catálogo de agentes:\n"
            f"{catalog_prompt}\nObjetivo: {goal}\n"
            'Responde SOLO con JSON: {"goal": "...", "steps": '
            '[{"agent_id": N, "input": {...}, "dependencies": [índices], '
            '"description": "..."}]}. Usa solo agentes del catálogo.'
        )

    def plan_goal(self, goal: str, catalog_prompt: str) -> Plan:
        prompt = self.build_prompt(goal, catalog_prompt)
        response_text = self._complete(prompt)
        payload = _extract_json(response_text)
        plan = Plan(
            goal=payload.get("goal", goal),
            steps=[
                PlanStep(
                    agent_id=step["agent_id"],
                    input=step.get("input", {}),
                    dependencies=step.get("dependencies", []),
                    conditions=step.get("conditions", []),
                    order=step.get("order", index),
                    description=step.get("description", ""),
                )
                for index, step in enumerate(payload.get("steps", []))
            ],
        )
        self.validate(plan)
        return plan

    def validate(self, plan: Plan) -> None:
        count = len(plan.steps)
        orders = []
        for index, step in enumerate(plan.steps):
            if step.agent_id == 0:
                raise ValueError(f"step {index}: agent_id must not be 0")
            if len(step.dependencies) != len(set(step.dependencies)):
                raise ValueError(f"step {index}: duplicate dependency")
            for dependency in step.dependencies:
                if dependency == index:
                    raise ValueError(f"step {index}: dependency cannot be self")
                if dependency < 0 or dependency >= count:
                    raise ValueError(f"step {index}: dependency {dependency} out of bounds")
            orders.append(step.order)
        if sorted(orders) != list(range(count)):
            raise ValueError("invalid order")
        self.execution_order(plan)

    @staticmethod
    def execution_order(plan: Plan) -> list[int]:
        # Kahn con desempate por el campo ``order`` de cada paso,
        # replicando el comportamiento del planner C++.
        import heapq

        indegree = {index: 0 for index in range(len(plan.steps))}
        dependents: dict[int, list[int]] = {index: [] for index in range(len(plan.steps))}
        for index, step in enumerate(plan.steps):
            for dependency in step.dependencies:
                dependents[dependency].append(index)
                indegree[index] += 1

        ready = [
            (plan.steps[index].order, index)
            for index, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(ready)

        result: list[int] = []
        while ready:
            _, index = heapq.heappop(ready)
            result.append(index)
            for dependent in dependents[index]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, (plan.steps[dependent].order, dependent))

        if len(result) != len(plan.steps):
            raise ValueError("cycle detected in plan dependencies")
        return result

    def _complete(self, prompt: str) -> str:
        if self.client is None:
            raise RuntimeError("planner requires an Anthropic/OpenAI client")
        if self.provider in {"anthropic", "claude"}:
            response = self.client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                max_tokens=1500,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        response = self.client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.0,
        )
        return response.choices[0].message.content

    @classmethod
    def from_environment(cls) -> "Planner":
        provider = os.getenv("SATELLITE_LLM_PROVIDER", "openai")
        if provider in {"anthropic", "claude"}:
            from anthropic import Anthropic

            return cls(Anthropic(), provider)
        from openai import OpenAI

        return cls(OpenAI(), provider)
