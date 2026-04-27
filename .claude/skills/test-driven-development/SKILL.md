---
name: test-driven-development
description: >
  Use when implementing any sub-task after task list has been approved.
  Triggers on: "start implementing", "let's go", "proceed", "work on
  <sub-task>", or any instruction to begin coding a specific sub-task.
---

# Test-Driven Development

One sub-task at a time. Plan first, tests second, code third. No exceptions.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Write code before the test? Delete it. Start over.

---

## Phase 1 — Plan the Sub-Task

Before writing any code or any test, open `docs/features/<feature-name>.md`
and add a `## Sub-task Plan` section at the top of the file.

This section must include:

```markdown
## Sub-task Plan: <sub-task name>

### What Is Being Implemented
[Exactly what this sub-task adds or changes — be specific]

### How It Will Be Implemented
[The approach — key logic, data flow, dependencies used]

### Key Functions
- `functionName(params) → returnType` — what it does
- `functionName(params) → returnType` — what it does

### Files Involved
- `path/to/file.ext` — what will be created or changed and why

### How It Will Be Tested
- **Unit** — what scenarios will be tested in isolation
- **Edge cases** — specific failure modes or boundary conditions to cover
```

Present this plan to the user and ask:
"Does this sub-task plan look right? Any changes before I start?"

Do not write any code or any test until the user explicitly approves.

---

## Phase 2 — Implement via TDD

Once the plan is approved, implement using strict RED-GREEN-REFACTOR.

**RED** — Write one failing test describing the exact behaviour this
sub-task must produce. Run it. Confirm it fails because the feature is
missing — not a syntax error or import problem.

**GREEN** — Write the minimum code to make it pass. Nothing more.

**VERIFY** — Run the full test suite. Every existing test must still pass.
Fix anything that broke before continuing.

**REFACTOR** — Clean names, remove duplication, extract helpers.
No new behaviour. All tests stay green.

### 5-Failure Rule

If the same test fails 5 or more times despite fix attempts — stop.
Do not attempt another fix. Present:

1. What the test expects
2. What is actually happening
3. What you think the root cause is
4. Options ranked by confidence — including whether the sub-task needs
   to be broken down or the design assumption is wrong
5. Your recommendation

Wait for the user's decision. If re-planning is needed, update the
Sub-task Plan, DESIGN.md, and TASKS.md before resuming.

---

## Phase 3 — Post-Implementation Verification

Before committing, verify the implementation matches the approved plan.

Go through each item in the Sub-task Plan and confirm:

- [ ] What was implemented matches "What Is Being Implemented"
- [ ] The approach matches "How It Will Be Implemented"
- [ ] All functions listed in "Key Functions" exist with correct signatures
- [ ] All files listed in "Files Involved" were created or modified as described
- [ ] All test scenarios in "How It Will Be Tested" are covered by actual tests

If anything does not match, fix it before proceeding.
If the implementation intentionally diverged from the plan, update the
plan to reflect what was actually built — then verify again.

---

## Phase 4 — Update Feature File and Commit

Once verification passes, clean up the feature file:

**1. Remove `## Sub-task Plan` from the feature file.**

**2. Fold the plan content into the permanent sections:**

- **Implementation** — describe what this sub-task added, based on
  "What Is Being Implemented" and "How It Will Be Implemented"
- **Key Files** — add functions and file descriptions from "Key Functions"
  and "Files Involved"
- **Testing** — add test scenarios from "How It Will Be Tested",
  updated to reflect what was actually written

**3. Update `docs/tasks/TASKS.md`**
```
- [x] <completed sub-task>
```

**4. Update `docs/DESIGN.md` if needed**
Only if something structural changed — architecture shifted, new decision
made, alternative ruled out, current state changed meaningfully.

**5. Commit**
```
git add .
git commit -m "feat(<feature>): <what this sub-task implemented>"
```
Message must correspond clearly to the TASKS.md sub-task so git log
can verify position in future sessions.

**6. Sub-task report**
- **Completed** — what was done in one or two sentences
- **Files created or modified** — every file touched
- **How to verify** — exact command to run
- **Manual setup required** — API keys, env vars, external services.
  If none, say so explicitly.
- **Next** — the next unchecked item in TASKS.md

---

## When All Tasks Are Complete

When every item in TASKS.md is checked off:

**1. Verify root `CLAUDE.md` is current**
- Project description still accurate?
- How to Use commands correct?
- Project Structure reflects actual directory layout?
- Environment Variables list complete?
- Conventions section reflects patterns used in the implementation?

**2. Verify `docs/DESIGN.md` is current**
- Current State section reflects what is now fully implemented?
- Architecture reflects decisions made during implementation?
- Setup Requirements complete?
- Open Questions resolved or still relevant?

**3. Final commit**
```
git add .
git commit -m "docs: update CLAUDE.md and DESIGN.md to reflect completed implementation"
```

**4. Final report to user**
- **What was built** — summary of all features completed
- **How to run it** — exact commands to get it working
- **Manual setup required** — everything the user must configure before running
- **What to do next** — open questions, known limitations, suggested next steps

---

## Rules

- One sub-task at a time. Do not start the next until this one is committed.
- No code before the sub-task plan is approved.
- No code before a failing test exists.
- Never declare done without running the full test suite.
- Post-implementation verification is mandatory — do not skip it.
- Hard to write tests = design signal. Raise it before pushing forward.
- 5 failures → apply the 5-failure rule. No exceptions.

## Rationalizations That Mean Start Over

- "I'll write the test after to verify it works"
- "This is too simple to need a test"
- "I already manually checked it"
- "Let me just get it working first"
