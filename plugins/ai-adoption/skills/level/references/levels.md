# AI adoption levels

This file is the source of truth for the level definitions. To change what a
level means, edit it here; the assessment in SKILL.md reads from this file.

## Level 1 — Assistance (AI-assisted)

Engineer drives. AI suggests.

- L1.1 Uses AI for code completion, suggestions and small code changes.
- L1.2 Writes and controls most of the implementation.
- L1.3 Reviews and accepts/rejects individual suggestions.
- L1.4 Needs to understand and manually integrate most AI output.

Typical behaviour: uses GitHub Copilot to write methods, generate tests or
explain code.

## Level 2 — Supervision (AI-directed)

Engineer directs. AI executes.

- L2.1 Delegates implementation of complete features or tasks.
- L2.2 AI can work across multiple files and understand broader context.
- L2.3 Engineer defines requirements, reviews implementation and validates results.
- L2.4 Can identify incorrect AI decisions and guide corrections.

Typical behaviour: uses Claude Code or Cursor to implement a feature, refactor
components or fix bugs across multiple files.

## Level 3 — Delegation (AI-delegating)

Engineer delegates. AI delivers.

- L3.1 Delegates substantial engineering tasks to autonomous agents.
- L3.2 Breaks down requirements into independently executable tasks.
- L3.3 Defines constraints, acceptance criteria and validation requirements.
- L3.4 Supervises agent execution rather than writing code.
- L3.5 Can manage an end-to-end workflow with limited intervention.

Typical behaviour: assigns an agent to implement an API, add tests, update
documentation and prepare a PR.

## Level 4 — Orchestration (AI-orchestrating)

Engineer orchestrates. AI teams execute.

- L4.1 Coordinates multiple agents working in parallel.
- L4.2 Decomposes complex initiatives into independent workstreams.
- L4.3 Manages dependencies, integration and conflicts between agents.
- L4.4 Establishes automated quality gates and verification.
- L4.5 Focuses on architecture, outcomes and system-level decisions.

Typical behaviour: coordinates several agents to implement a feature across
multiple services, including API, frontend, tests and documentation.
