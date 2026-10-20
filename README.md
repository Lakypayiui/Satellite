# Satellite

**An AI agent workspace for software work.**

Satellite turns a repository into a workspace you can direct in plain language:
describe a goal and it plans and executes it over your project using reusable
agents and skills.

## What it does

- Works on real software tasks: add features, fix bugs, refactor, write tests,
  explore and document a codebase.
- Turns a goal into an executable plan and runs independent steps in parallel.
- Splits large, separable work into parallel subagents when it is worth it.
- Uses reusable agents and skills, and can propose new ones with human approval.
- Remembers a compact summary of earlier turns so long sessions stay coherent.
- Searches the internet, reads pages and downloads files.
- Generates Mermaid diagrams and shows them as SVG.
- Can control desktop applications when you enable it, with your approval and an
  app allowlist.
- Protects secrets and personal data before sending content to a provider.
- Lets you choose how permissions are handled: ask, auto or unrestricted.

## Where you can use it

- Command line.
- Interactive terminal chat.
- Desktop app.
- MCP server.

## Base agents

- `orchestrator` — turns a goal into a plan using the available catalog.
- `agent_creator` — proposes a reusable agent for a capability you are missing.
- `skill_curator` — keeps the skill catalog organized.
- `pele_diagram_agent` — designs and renders diagrams.
- `desktop_operator` — opens apps and operates desktop windows.
- `web_researcher` — searches the web, reads pages and downloads files.

## Typical requests

- "Add a health endpoint and tests."
- "Find and fix the bug in the login flow."
- "Explain how this project works."
- "Draw a diagram of the architecture."
- "Research this library and summarize the trade-offs."
- "Download this dataset into the project."
