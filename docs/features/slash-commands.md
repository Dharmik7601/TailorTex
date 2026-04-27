# Feature: Slash Commands & Local CLI

## Files Involved

| File | Role |
|------|------|
| `.claude/commands/tailor-resume.md` | `/tailor-resume` slash command definition |
| `.claude/commands/judge-resume.md` | `/judge-resume` slash command definition |
| `.claude/commands/optimize-prompt.md` | `/optimize-prompt` slash command definition |
| `local/main.py` | Legacy CLI entry point (Gemini API, no server) |
| `local/compile.py` | Standalone LaTeX compiler script |
| `local/backup.py` | Backup output PDFs to `BACKUP_LOCATION` |
| `Makefile` | `make run`, `make claude`, `make compile`, `make backup`, `make clean` |
| `job_description.txt` | JD file read by the CLI and written by `ClaudeCliProvider` |

No automated tests for slash commands or the local CLI.

---

## Slash Commands Overview

Claude Code slash commands are Markdown files in `.claude/commands/`. Each file declares:
- `description` — shown in the slash command picker
- `argument-hint` — documents the expected `$ARGUMENTS`
- `allowed-tools` — restricts which Claude tools the command may use

Commands are invoked as `/tailor-resume Google_SWE` — the argument becomes `$ARGUMENTS` inside the command body.

---

## `/tailor-resume <NAME>`

**File:** `.claude/commands/tailor-resume.md`
**Allowed tools:** `Read`, `Write`
**Invoked by:** `ClaudeCliProvider` via `claude -p /tailor-resume {company_name}` and `make claude NAME=Company`

### Purpose

Generates `output/<NAME>_Resume.tex` from the master resume and job description, applying all rules from `system_prompt.txt`. Writes only the `.tex` file — the caller handles compilation, extras writing, and PDF opening.

### Step-by-Step Execution

```
1. Read resumes/master_resume.tex
   ├─ Split at \begin{document}
   ├─ preamble = everything before (including) \begin{document}
   └─ body = \begin{document} onward
   → preamble is stored and NEVER modified

2. Read job_description.txt
   → target role to tailor for

3. Read prompts/system_prompt.txt
   Read prompts/user_constraints.txt   (if non-empty)
   Read prompts/additional_projects.txt (if non-empty)
   → generation rules + personal constraints + project bank

4. Generate tailored resume body
   → apply all rules from step 3 to master body, targeting the JD from step 2
   → formatting_constraints in system_prompt guarantee one-page fit

5. Reassemble: preamble + generated body
   Write to output/<NAME>_Resume.tex

6. Confirm file exists
   → backend handles compilation, cleanup, opening the PDF
```

### Key Invariant

The command writes **only** `output/<NAME>_Resume.tex`. It never:
- Compiles the `.tex` to PDF
- Writes to `output/extras/`
- Reads or writes `prompts/daily_feedback.json`
- Opens the PDF

This invariant is what makes the command composable: the server (`ClaudeCliProvider`), the Makefile (`make claude`), and a human can all invoke it and handle the post-generation steps differently.

### File Access Pattern

| Operation | File |
|-----------|------|
| Read | `resumes/master_resume.tex` |
| Read | `job_description.txt` |
| Read | `prompts/system_prompt.txt` |
| Read | `prompts/user_constraints.txt` (if non-empty) |
| Read | `prompts/additional_projects.txt` (if non-empty) |
| Write | `output/<NAME>_Resume.tex` |

---

## `/judge-resume <NAME>`

**File:** `.claude/commands/judge-resume.md`
**Allowed tools:** `Read`, `Write`
**Invoked by:** User manually after generating a resume

### Purpose

Scores a generated resume against its corresponding job description using the evaluator rubric. Appends the full evaluation result to `prompts/daily_feedback.json` and outputs only `{"total_score": N}` to stdout.

### Step-by-Step Execution

```
1. Read output/extras/<NAME>_Resume.txt
   → plain-text extract written by the backend after generation
   → contains Technical Skills, Experience, Projects, Education
   → no LaTeX commands, no binary artifacts

2. Read output/extras/<NAME>_jd.txt
   → JD snapshot written by the backend at generation time
   → isolated per-job; guaranteed to match the JD used for this resume

3. Read prompts/prompt_summary.txt
   → compressed rule reference (used as system_prompt + user_constraints context)

4. Read prompts/evaluator_prompt.txt
   → role definition, scoring categories, output schema, rule_reference_map

5. Apply full evaluator rubric:
   → resume_id = <NAME>
   → score 4 categories (KEYWORD 30%, BULLET 40%, BELI 20%, ACCT 10%)
   → compute total_score, passed (>= 72), failures[], constrained_failures[], new_patterns[]

6. Read prompts/daily_feedback.json
   → if valid JSON array: append evaluation result object
   → if missing/empty/invalid: create new array with only this result
   → write updated array back to prompts/daily_feedback.json

7. Output ONLY to stdout:
   {"total_score": <number>}
```

### Why `output/extras/` Files

The backend writes both files at the end of generation:
- `output/extras/<NAME>_Resume.txt` — `format_resume_for_eval(tex)` output from `tex_parser.py`
- `output/extras/<NAME>_jd.txt` — the raw JD string passed to `POST /generate`

Storing these as snapshots ensures the evaluator scores the resume against the exact JD and resume state at generation time, even if files are later modified or the server restarts.

### File Access Pattern

| Operation | File |
|-----------|------|
| Read | `output/extras/<NAME>_Resume.txt` |
| Read | `output/extras/<NAME>_jd.txt` |
| Read | `prompts/prompt_summary.txt` |
| Read | `prompts/evaluator_prompt.txt` |
| Read + Write | `prompts/daily_feedback.json` |

---

## `/optimize-prompt`

**File:** `.claude/commands/optimize-prompt.md`
**Allowed tools:** `Read`, `Write`
**Invoked by:** User manually after accumulating ≥ 5 evaluations

### Purpose

Runs the full optimizer loop — reads `daily_feedback.json`, applies the 6-step optimizer algorithm, updates prompt files, and clears the feedback accumulator.

### Step-by-Step Execution

```
1. Read prompts/system_prompt.txt     → current generation prompt (target of optimization)
2. Read prompts/user_constraints.txt  → immutable personal config (reference only, never modified)
3. Read prompts/change_tracker.json   → scoring ledger and rule history
4. Read prompts/daily_feedback.json   → array of evaluation results from recent judge runs
5. Read prompts/optimizer_prompt.txt  → full optimization rules, decision constraints, output schema
6. Read prompts/prompt_summary.txt    → compressed rule reference

7. Construct optimizer inputs:
   SYSTEM_PROMPT   = content of system_prompt.txt
   CHANGE_TRACKER  = content of change_tracker.json
   DAILY_FEEDBACK  = array from daily_feedback.json
   RUN_METADATA    = { run_date, total_resumes_evaluated, jd_domains }

8. Apply optimizer logic (see feedback-loop.md for the 6-step algorithm)

9. Write system_prompt.txt  ← ONLY if action_taken == "PROMPT_MODIFIED"
   (NO_CHANGE, SCORES_UPDATED, SKIPPED_INSUFFICIENT_SAMPLE → do NOT overwrite)

10. Write change_tracker.json  ← ALWAYS, regardless of action_taken

11. If action_taken == "PROMPT_MODIFIED":
    Update prompt_summary.txt to mirror rule changes:
    → added rule: append to appropriate block
    → removed rule: delete its line
    → rewritten rule: replace its line

12. Write [] to daily_feedback.json  ← ALWAYS (unconditional clear)

13. Print plain-text summary to stdout:
    action_taken, total_resumes_evaluated, each changes_made entry,
    deferred_patterns, whether prompt_summary.txt was updated
```

### Output Files Summary

| File | Written when |
|------|-------------|
| `prompts/system_prompt.txt` | Only if `action_taken == "PROMPT_MODIFIED"` |
| `prompts/change_tracker.json` | Always |
| `prompts/prompt_summary.txt` | Only if system_prompt rules changed |
| `prompts/daily_feedback.json` | Always — cleared to `[]` |

### File Access Pattern

| Operation | File |
|-----------|------|
| Read | `prompts/system_prompt.txt` |
| Read | `prompts/user_constraints.txt` |
| Read | `prompts/change_tracker.json` |
| Read | `prompts/daily_feedback.json` |
| Read | `prompts/optimizer_prompt.txt` |
| Read | `prompts/prompt_summary.txt` |
| Conditional Write | `prompts/system_prompt.txt` |
| Write | `prompts/change_tracker.json` |
| Conditional Write | `prompts/prompt_summary.txt` |
| Write | `prompts/daily_feedback.json` (cleared to `[]`) |

---

## Local CLI (`local/main.py`)

The CLI is a self-contained Gemini-only implementation that predates the backend. It does not use `prompt_pipeline.py`, the provider registry, or any backend modules — it duplicates the prompt assembly logic inline.

### Entry Point

```bash
make run NAME=Google                          # use job_description.txt, append constraints + projects
make run NAME=Google CONSTRAINTS=false PROJECTS=false  # skip optional files
```

Makefile maps to:
```bash
python local/main.py --jd job_description.txt --output "Google" [--constraints] [--projects]
```

### Execution Flow

```
argparse → --jd, --output, --prompt, --constraints, --projects
Load resumes/master_Resume.tex  (hardcoded path, note capital R)
Load job_description.txt
Split preamble at \begin{document}
Load prompts/system_prompt.txt
Optionally append user_constraints.txt
Optionally append additional_projects.txt
Assemble user_prompt (XML-tagged, same order as prompt_pipeline.py)
Call Gemini API: gemma-4-31b-it (hardcoded, no waterfall config)
extract_latex() → sanity check delimiters → write output/{NAME}_Resume.tex
compile_latex() via subprocess → open PDF (Windows: cmd /c start)
```

**Differences from backend:**
- No `prompt_pipeline.py` shared logic — prompt assembly is duplicated inline
- Hardcoded model (`gemma-4-31b-it`), no waterfall fallback
- No `output/extras/` files written — eval/optimizer loop not available
- No SSE, no job queue, no server communication
- Hardcoded resume path (`resumes/master_Resume.tex` with capital R)

### Other Local Scripts

**`local/compile.py`** — standalone script invoked by `make compile NAME=Company`. Re-runs `pdflatex` on an existing `.tex` file without re-generating via AI.

**`local/backup.py`** — invoked by `make backup`. Reads `BACKUP_LOCATION` from `.env`, copies all PDFs in `output/` to that location with a date-injected filename (`{Company}_{9thMarch2026}_Resume.pdf`). Parses company name from filename prefix (text before first `-` or `_`).

### Makefile Targets

| Target | Command | Description |
|--------|---------|-------------|
| `make run NAME=X` | `python local/main.py --jd ... --output X` | Generate via Gemini API |
| `make claude NAME=X` | `claude -p "/tailor-resume X"` | Generate via Claude Code CLI |
| `make compile NAME=X` | `python local/compile.py ...` | Re-compile existing `.tex` |
| `make backup` | `python local/backup.py` | Backup PDFs to `BACKUP_LOCATION` |
| `make clean` | `del output/*.{pdf,tex,...}` | Clear all output files |
| `make serve-api` | `uvicorn api.server:app --port 8001` | Start FastAPI backend |
| `make serve-ui` | `npm run dev` | Start React frontend |
| `make dev` | `make -j2 serve-api serve-ui` | Start both in parallel |
| `make setup` | Create venv + pip install | First-time setup |
