---
name: initializing-project
description: >
  Use once at the start of a new project to create the directory structure
  and repo-specific CLAUDE.md. Triggers on: "initialize", "set up the
  project", "create the project structure". Skips anything that already exists.
---

# Initializing Project

One-time setup. Check first, create only what is missing.

## Check First
If `docs/tasks/TASKS.md` already exists, tell the user and stop.
Use creating-tasks to add new features instead.

## Create

**`docs/features/`** — empty, for feature LLD files.

**`docs/tasks/`** — empty, for TASKS.md.

**`docs/tasks/TASKS.md`** — only if missing:
```markdown
# Tasks
```

**`docs/DESIGN.md`** — only if missing (brainstorming should have
created it). If absent, create a placeholder:
```markdown
# Design Document
<!-- Run brainstorming skill to populate this -->
```

**Root `CLAUDE.md`** — this is the project orientation file.
If `/init` has already been run and a CLAUDE.md exists, replace the
content with the full template below, preserving any existing commands
or conventions already captured.
If none exists, create it from scratch.

```markdown
# <Project Name>

<What this project does — 2-3 sentences. What problem it solves,
who uses it, what it produces.>

## How to Use
- Install dependencies: `<command>`
- Configure environment: copy `.env.example` to `.env` and fill in values
- Run in development: `<command>`
- Run tests: `<command>`
- Build for production: `<command>`

## Project Structure
<Top-level directory tree with a one-line description of each part>
```
project/
├── docs/
│   ├── DESIGN.md        # full design document — architecture and decisions
│   ├── features/        # one LLD file per feature
│   └── tasks/           # TASKS.md — current task progress
├── src/                 # <what lives here>
└── tests/               # <what lives here>
```

## Key Files
- `CLAUDE.md` — this file, project orientation for humans and Claude
- `docs/DESIGN.md` — architecture, data models, decisions, alternatives
- `docs/tasks/TASKS.md` — task checklist, updated after each sub-task
- `docs/features/<name>.md` — per-feature detail: implementation and tests

## Environment Variables
<List each required variable name and what it is for — no values>
- `VAR_NAME` — what it controls

## Conventions
<Patterns, naming rules, or architectural decisions established in this repo>
```

**`.gitignore`** — only if missing:
```
node_modules/
.venv/
__pycache__/
*.pyc
.env
.env.local
dist/
build/
*.egg-info/
.DS_Store
Thumbs.db
*.log
.idea/
.vscode/
*.swp
```

No `git init` — that is the user's responsibility.

## Report
List what was created and what was skipped.
"Structure ready. Tell me to create the tasks when ready."
