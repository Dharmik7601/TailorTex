---
name: brainstorming
description: >
  Use for any new project or any change to an existing project, before
  writing any code. Triggers on: "build", "new feature", "add", "change",
  "I want to create", "help me plan", "update", "modify".
---

# Brainstorming

No code, no files, no implementation decisions until the user explicitly
approves the plan.

## Step 1 — Read existing context
If a project already exists:
- Read root `CLAUDE.md` to understand what the project is
- Read `docs/DESIGN.md` to understand current architecture and decisions
- Read `docs/tasks/TASKS.md` to understand what is already done
Do this before asking any questions.

## Step 2 — Clarify
One question at a time. Focus on purpose, constraints, and success criteria.
If the user provides a detailed plan, evaluate it — suggest improvements
or alternatives if you see them, then restate the plan in your own words
to confirm shared understanding.
If scope is too large, help decompose it first.

## Step 3 — Propose approach
Present 2-3 options with honest tradeoffs. Lead with your recommendation
and explain why. Apply YAGNI — cut anything not needed for this goal.
Record rejected options in DESIGN.md Alternatives Considered.

## Step 4 — Present plan and get approval
Present the full plan clearly. Ask:
"Does this plan look right? Any changes before I proceed?"
Do not move forward until the user explicitly approves.

## Step 5 — Write or update docs/DESIGN.md
On approval, update DESIGN.md. If it does not exist, create it.
Update only sections affected by this session.

DESIGN.md required sections:

```
# Design Document

## Purpose and Scope
What this project does and what it explicitly does not do.

## Current State
What is implemented and working. Starts as "Not yet started."
Updated as features complete.

## System Overview
How the system works end to end in plain language.

## Architecture
Components, how they connect, data flow, key patterns and why they
were chosen.

## Project Structure
Full directory layout with a brief note on what each part contains.

## Data Models
Key data structures, schemas, or types with field names and types.

## API / Interface Contracts
Public interfaces, endpoints, or function signatures other components
depend on. Include input/output shapes.

## Error Handling Strategy
How errors are caught, surfaced, logged, and communicated.

## Security Considerations
Auth, data validation, secrets management, known risks and mitigations.

## Performance Considerations
Expected load, bottlenecks, caching strategy if applicable.

## Testing Strategy
Types of tests, frameworks, coverage expectations.
Detailed per-feature test approach lives in docs/features/<name>.md.

## Setup Requirements
Everything the user must configure manually: API keys, environment
variables, external services. Be explicit and complete.

## Alternatives Considered
For each major decision: what else was considered and why it was rejected.

## Open Questions
Unresolved decisions or things to revisit later.
```

## Step 6 — Commit DESIGN.md

## Step 7 — Hand off
For a new project: "Plan approved and saved. Run initializing-project
to set up the structure, then tell me to create the tasks."
For changes to an existing project: "Plan approved and saved. Tell me
to create the tasks when ready."
Do not initialize or create tasks automatically.
