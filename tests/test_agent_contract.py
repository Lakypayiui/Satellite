"""Tests del contrato de agentes: rol vs tarea, extend, skill y límites."""

import asyncio
import json

from satellite.agents.creator import AgentProposalGenerator, CreatorOutcome
from satellite.agents.expander import AgentExpander
from satellite.agents.prompts import DefaultPromptRegistry
from satellite.agents.registry import DefaultAgentRegistry
from satellite.agents.validator import AgentProposalValidator
from satellite.approval.base import AutoApprovalPort
from satellite.approval.policy import ApprovalPolicy
from satellite.core.proposal import AgentExtension, ProposedAgent, ProposedSkill
from satellite.core.spec import AgentSpec
from satellite.kernel.policy import PolicyMode
from satellite.providers.fake import FakeLLM
from satellite.skills.registry import SkillRegistry
from satellite.tools.fs import ReadFileTool
from satellite.tools.registry import DefaultToolRegistry


def run(coro):
    return asyncio.run(coro)


def make_registry(*specs) -> DefaultAgentRegistry:
    registry = DefaultAgentRegistry()
    for spec in specs:
        registry.register_spec(spec)
    return registry


def docs_spec() -> AgentSpec:
    return AgentSpec(
        id="docs_writer",
        name="Docs Writer",
        capabilities=("write_docs", "structure_docs"),
        prompt_ref="docs_writer",
    )


def make_proposal(**overrides) -> ProposedAgent:
    data = dict(
        id="docs_writer",
        name="Docs Writer",
        description="redacta documentación técnica",
        capabilities=("write_docs", "structure_docs"),
        tools=("read_file",),
        model_role="generated",
        system_prompt="# ROL\nRedacta documentación.",
        user_template="Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
        example_tasks=("escribir un README", "documentar una API", "redactar una guía"),
        rationale="r",
        reuse_checked=True,
    )
    data.update(overrides)
    return ProposedAgent(**data)


def make_validator() -> AgentProposalValidator:
    return AgentProposalValidator(
        make_registry(), tool_ids={"read_file"}, allowed_roles=("generated",)
    )


# --- límites del rol ---

def test_single_capability_rejected() -> None:
    report = make_validator().validate(make_proposal(capabilities=("only_one",)))
    assert report.ok is False
    assert any("between 2 and 6" in error for error in report.errors)


def test_too_many_capabilities_rejected() -> None:
    report = make_validator().validate(
        make_proposal(capabilities=("a1", "b2", "c3", "d4", "e5", "f6", "g7"))
    )
    assert report.ok is False
    assert any("between 2 and 6" in error for error in report.errors)


def test_too_many_tools_rejected() -> None:
    report = make_validator().validate(make_proposal(tools=("read_file",) * 9))
    assert report.ok is False
    assert any("too many tools" in error for error in report.errors)


def test_missing_example_tasks_rejected() -> None:
    report = make_validator().validate(make_proposal(example_tasks=("solo una", "y dos")))
    assert report.ok is False
    assert any("example_tasks" in error for error in report.errors)


def test_duplicate_example_tasks_rejected() -> None:
    report = make_validator().validate(make_proposal(example_tasks=("a", "a", "b")))
    assert report.ok is False
    assert any("duplicate" in error for error in report.errors)


def test_two_responsibilities_rejected() -> None:
    report = make_validator().validate(
        make_proposal(description="escribe documentación y además despliega en producción")
    )
    assert report.ok is False
    assert any("split it" in error for error in report.errors)


def test_valid_role_accepted() -> None:
    report = make_validator().validate(make_proposal())
    assert report.ok is True


# --- parsing del creator ---

def make_generator(script, registry=None, validator=None) -> AgentProposalGenerator:
    prompts = DefaultPromptRegistry()
    prompts.register_pair(
        "agent_creator",
        "N:{task} C:{context} F:{feedback} S:{output_schema} T:{tools}",
        "Tarea: {task}\nContexto: {context}\nFeedback: {feedback}",
    )
    tools = DefaultToolRegistry()
    tools.register(ReadFileTool())
    return AgentProposalGenerator(
        FakeLLM(script=script),
        registry or make_registry(),
        prompts,
        tools,
        validator=validator,
    )


def test_creator_parses_extend() -> None:
    payload = {
        "type": "extend",
        "agent_id": "docs_writer",
        "add_capabilities": ["translate_docs"],
        "rationale": "faltaba traducir",
    }
    outcome = run(make_generator([{"content": json.dumps(payload)}]).propose("traducir docs"))
    assert outcome.kind == "extend"
    assert outcome.extension is not None
    assert outcome.extension.agent_id == "docs_writer"
    assert outcome.extension.add_capabilities == ("translate_docs",)


def test_creator_parses_skill_suggestion() -> None:
    payload = {
        "type": "skill_suggestion",
        "id": "csv_to_json",
        "description": "convierte csv a json",
        "language": "python",
        "entrypoint": "main.py",
        "script": "print(1)",
        "rationale": "transformación determinista",
    }
    outcome = run(make_generator([{"content": json.dumps(payload)}]).propose("convertir csv"))
    assert outcome.kind == "skill_suggestion"
    assert outcome.skill is not None
    assert outcome.skill.id == "csv_to_json"


def test_creator_retries_when_limits_violated() -> None:
    bad = {
        "type": "proposed_agent",
        "id": "docs_writer",
        "name": "Docs Writer",
        "description": "d",
        "capabilities": ["one"],
        "tools": [],
        "model_role": "generated",
        "system_prompt": "# ROL",
        "user_template": "Tarea: {task}",
        "example_tasks": ["a", "b", "c"],
        "rationale": "r",
        "reuse_checked": True,
    }
    generator = make_generator(
        [{"content": json.dumps(bad)}, {"content": json.dumps(bad)}],
        validator=make_validator(),
    )
    outcome = run(generator.propose("docs"))
    assert outcome.kind == "error"
    assert "between 2 and 6" in (outcome.error or "")


# --- expander: extend y skill ---

class StubCreator:
    def __init__(self, outcome: CreatorOutcome) -> None:
        self.outcome = outcome

    async def propose(self, need, *, extra_context=""):
        return self.outcome


def make_expander(registry, outcome, *, skills=None) -> AgentExpander:
    return AgentExpander(
        StubCreator(outcome),
        registry,
        DefaultPromptRegistry(),
        policy=ApprovalPolicy(PolicyMode.ASK),
        skills=skills,
    )


def test_expander_extends_existing_agent() -> None:
    registry = make_registry(docs_spec())
    outcome = CreatorOutcome(
        kind="extend",
        extension=AgentExtension(agent_id="docs_writer", add_capabilities=("translate_docs",)),
    )
    result = run(make_expander(registry, outcome).expand("traducir docs", approval_port=AutoApprovalPort()))
    assert result.kind == "extended"
    assert "translate_docs" in registry.get("docs_writer").capabilities


def test_expander_rejects_reserved_extension() -> None:
    outcome = CreatorOutcome(
        kind="extend",
        extension=AgentExtension(agent_id="orchestrator", add_capabilities=("x",)),
    )
    result = run(make_expander(make_registry(), outcome).expand("x", approval_port=AutoApprovalPort()))
    assert result.kind == "error"


def test_expander_rejects_extension_over_limit() -> None:
    registry = make_registry(
        AgentSpec(
            id="docs_writer",
            name="D",
            capabilities=("a1", "b2", "c3", "d4", "e5", "f6"),
            prompt_ref="docs_writer",
        )
    )
    outcome = CreatorOutcome(
        kind="extend",
        extension=AgentExtension(agent_id="docs_writer", add_capabilities=("g7",)),
    )
    result = run(make_expander(registry, outcome).expand("x", approval_port=AutoApprovalPort()))
    assert result.kind == "rejected"


def test_expander_creates_skill_when_script_present(tmp_path) -> None:
    skills = SkillRegistry(tmp_path / "skills")
    outcome = CreatorOutcome(
        kind="skill_suggestion",
        skill=ProposedSkill(
            id="csv_to_json",
            description="convierte csv a json",
            entrypoint="main.py",
            script="print(1)",
            rationale="determinista",
        ),
    )
    result = run(
        make_expander(make_registry(), outcome, skills=skills).expand(
            "convertir csv", approval_port=AutoApprovalPort()
        )
    )
    assert result.kind == "skill_created"
    assert skills.get("csv_to_json").id == "csv_to_json"


def test_expander_suggests_skill_without_script(tmp_path) -> None:
    skills = SkillRegistry(tmp_path / "skills")
    outcome = CreatorOutcome(
        kind="skill_suggestion",
        skill=ProposedSkill(
            id="csv_to_json", description="convierte csv a json", entrypoint="main.py", rationale="r"
        ),
    )
    result = run(
        make_expander(make_registry(), outcome, skills=skills).expand(
            "convertir csv", approval_port=AutoApprovalPort()
        )
    )
    assert result.kind == "skill_suggested"
    assert skills.count() == 0
