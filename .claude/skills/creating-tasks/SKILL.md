---
name: creating-tasks
description: >
  Use when the user says to create tasks after plan approval. Triggers on:
  "create tasks", "make the task list", "break this into tasks".
---

# Creating Tasks

Translate an approved plan into a concrete task list, then get explicit
approval before implementation begins.

## Before Running
- `docs/DESIGN.md` must exist — run brainstorming first if missing
- `docs/features/` and `docs/tasks/` must exist — run initializing-project first

## What to Create

**`docs/tasks/TASKS.md`** — add new sections if it exists, create if not.

**`docs/features/<feature-name>.md`** — one file per feature.
Start with what is known from the design. Implementation and Key Files
are filled in progressively during the TDD phase.

## TASKS.md Format

```markdown
# Tasks

## <feature-name>
- [ ] <sub-task>
- [ ] <sub-task>
- [ ] Integration tests for <feature-name>

## <another-feature>
- [ ] <sub-task>
- [ ] Integration tests for <another-feature>

## End-to-End
- [ ] End-to-end smoke test
```

Always add an integration test sub-task at the end of each feature block.
Always add an end-to-end task after all features.
Each sub-task is implemented via the TDD skill — do not describe
implementation steps here.

## docs/features/<feature-name>.md Format

```markdown
# <Feature Name>

## What It Does
What this feature is responsible for and why it exists.

## Implementation
How it works — approach, key logic, data flow within this feature.
Starts empty. Updated after each sub-task completes.

## Key Files
Every file that belongs to this feature.
- `path/to/file.ext` — what it does
  - `functionName(params)` — what it does
  - `functionName(params)` — what it does
Starts empty. Updated as implementation progresses.

## Testing
How this feature is tested:
- **Unit** — what is tested in isolation and with what framework
- **Integration** — how this feature is tested alongside other parts
- **Edge cases** — empty input, auth failure, timeouts, concurrent writes, etc.
Starts empty. Updated as tests are written.
```

## Approval Gate

After creating TASKS.md and all feature files, show the full TASKS.md
to the user and ask:
"Does this task list look right? Say yes to begin implementation or
let me know what to change."

Do not trigger implementation or the TDD skill until the user explicitly
confirms the task list.
