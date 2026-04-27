# Design Document

## Overview

TailorTex is a resume tailoring system that takes a base LaTeX resume and a job description, calls an AI model, and produces a one-page tailored PDF. It operates in two modes that share the same prompt logic but differ in orchestration: a local CLI (Gemini API, direct invocation) and a Chrome side-panel extension backed by a FastAPI server (Gemini API or Claude Code, multi-job queue).

---

## Architecture Diagram

```
  Chrome Extension          Local CLI
  (popup.js + SSE)          (local/main.py)
        │                         │ direct
        ▼                         │
  FastAPI Backend           Gemini API                          
        │                         │
  ┌─────┴──────┐                  │
  │ per-method │                  │
  │  queues +  │                  │
  │  workers   │                  │
  └─────┬──────┘                  │
        │                         │
        ▼                         ▼
  prompt_pipeline.py  ─────────────
  (split preamble, load prompts, assemble)
        │
  Provider Registry
  ┌─────┴──────────────┐
  ▼                    ▼
GeminiProvider    ClaudeCliProvider
(API waterfall)   (claude -p /tailor-resume)
  └──────┬──────────────┘
         ▼
    compiler.py
    (pdflatex → PDF)
         │
         ▼
    output/{Company}_Resume.pdf
```

---

## Overall Request Flow (Extension → PDF)

```
1. User fills form in extension popup (company, JD, resume, method, location)
2. POST /generate  →  server returns {job_id}
3. Extension stores job in chrome.storage.local, opens EventSource to /status/{job_id}
4. Server enqueues payload to _work_queues[method]
5. Worker thread dequeues, calls _run_generation():
   a. _replace_location() — patches header block in-place
   b. build_prompts()     — splits preamble, loads prompt files, assembles prompts
   c. get_provider(method).generate(request):
      ─ Gemini:     call API → postprocess → validate → write .tex → compile → .pdf
      ─ ClaudeCLI:  write JD file → claude -p /tailor-resume → validate → compile → .pdf
   d. os.startfile(pdf_path)  — auto-opens PDF in system viewer
   e. jobs[job_id]["status"] = "completed"
6. SSE generator emits "completed" event; extension closes EventSource
7. Extension updates chrome.storage → renders "completed" card with Open PDF / View Details
```

---

## Local CLI Implementation (`local/`)

The CLI (`local/main.py`) is a self-contained Gemini-only implementation that predates the backend architecture. It duplicates prompt assembly logic from `prompt_pipeline.py` and uses a hardcoded model list without waterfall config.

**Entry point:** `make run NAME=Company`

```
local/main.py
  argparse  →  --jd, --output, --constraints, --projects
  Load master_resume.tex + job_description.txt
  Split preamble at \begin{document}
  Assemble system_prompt + user_prompt
  Call Gemini API (gemma-4-31b-it)
  extract_latex() → sanity check → write .tex
  compile_latex() → open PDF
```

The CLI does not write `extras/` files, does not support the eval/optimizer loop, and does not communicate with the backend. It is the legacy entry point and is not tested.

---

## Chrome Extension Architecture (`frontend/extension/`)

### Manifest and Side Panel

The extension uses Chrome MV3's side panel API. `background.js` opens the side panel on the toolbar icon click. The side panel is always-on — it does not close between page navigations.

### State Model

All job state lives in `chrome.storage.local` under key `ttjobs` (array). This means:
- State survives side panel close/reopen
- All open side panels across tabs stay in sync via `chrome.storage.onChanged`
- No job state is lost on extension reload (only requires backend to still hold the `job_id`)

```js
// Job object shape in storage
{
  job_id: "uuid",
  company: "Google",
  resume_name: "resumes/master_resume.tex",
  method: "gemini" | "claudecli",
  status: "queued" | "running" | "completed" | "error",
  submitted_at: 1234567890,
  log: []          // populated only at completion/error, not during streaming
}
```

### SSE Connection Management

Live log lines are buffered in `logCache` (Map in memory, per tab, per session) and written directly to the card's `<pre>` element — no storage writes during streaming. At `completed`/`error`, the full buffered log is written to storage once. This avoids O(n²) storage writes as log lines accumulate.

The `sseMap` (Map of `job_id → EventSource`) prevents duplicate connections when `renderQueue` is called multiple times. A `completedReceived` boolean flag prevents the `onerror` handler from overwriting a completed job when the server closes the stream normally after sending `completed`.

### Reconciler Pattern

`renderQueue(jobs)` is a reconciler, not a full DOM rebuild. It:
1. Patches existing cards with `patchJobCard()` (no `innerHTML` reset → preserves event listeners)
2. Creates cards for new jobs
3. Removes cards for discarded jobs
4. Re-appends all cards in newest-first order (uses `appendChild` which moves without cloning)

### JD Auto-Extraction

On DOMContentLoaded, `extractFromPage()` runs `chrome.scripting.executeScript` on the active tab to scrape the job description and company name from common job board DOM selectors (LinkedIn, Indeed, Workday, Greenhouse, Lever). Falls back to `document.title` regex for company name.

### Output Browser

The "Browse Output" view lists `output/` resumes (via `GET /output/resumes`) that have both `.tex` and `.pdf` present. These are "archived" resumes with no live `job_id` in the server's in-memory store. All requests for archived resumes use `job_id='_'`, which the server cannot find in `jobs`, triggering the company-name fallback path on every endpoint (`/open`, `/details`, `/recompile`, `/files`).

---

## Backend Architecture (`backend/`)

### FastAPI Application

`server.py` is the sole FastAPI app. All configuration is at module level:
- `LOCATIONS` list — append to add new location options
- `_work_queues` dict — auto-derived from provider registry at import time
- `jobs` dict — in-memory, lost on restart

### Per-Method Worker Queue Design

```python
_work_queues = {pid: queue.Queue() for pid in registered_provider_ids()}
# → {"gemini": Queue(), "claudecli": Queue()}
```

One daemon `threading.Thread` per method, started at import time. Workers call `queue.get()` (blocking, zero CPU spin). This gives:
- Gemini jobs: sequential (one worker drains one queue)
- Claude jobs: sequential (one worker drains one queue)
- Gemini + Claude simultaneously: parallel (separate queues, separate threads)

The design intentionally serializes same-method jobs because both Gemini (rate limits) and Claude CLI (single process, file writes) benefit from serialization.

### Location Replacement

Before prompt assembly, `_replace_location()` patches the `{City, ST, Country}` pattern exclusively inside the `\begin{center}...\end{center}` header block using a two-level regex: outer captures the center block, inner replaces the first `{City, State, Country}` pattern within it. This prevents false matches in the Education section (e.g. `{Rochester, NY, USA}` in a university name).

### Endpoint Fallback Pattern

Several endpoints (`/open`, `/details`, `/recompile`, `/files`) accept both a `job_id` path parameter and an optional `company` query parameter. The lookup order is:
1. Check `job_id in jobs` (in-memory, live jobs)
2. If not found, reconstruct path from `company` query param

This makes all endpoints work after server restart, using `output/{company}_Resume.{ext}` as the deterministic path.

### Retry on Error — Job Details Persistence

When `POST /generate` is called, all generation inputs are persisted to
`output/job_details/{company_name}.json` before the job is enqueued. This file survives server
restarts, browser/extension resets, and is independent of the in-memory job store.

`GET /job_details/{company}` reads and returns this file as a `JobDetails` response. The
Chrome extension's Retry button calls this endpoint on an error card, then re-submits the same
inputs to `/generate` as a new job.

`DELETE /files/{job_id}` also removes `output/job_details/{company_name}.json` so no orphaned
details files are left behind after a resume is fully deleted.

If the original submission used a named resume (`resume_name` starts with `resumes/`), the retry
re-uses `resume_name` and the server re-reads from disk — picking up any edits made since the
original submission. If the original submission used a file upload, `master_resume_tex` content
is stored in the JSON and sent as a file blob on retry.

### Provider Registry Pattern

```
core/providers/__init__.py   _REGISTRY dict
                              _register(GeminiProvider())
                              _register(ClaudeCliProvider())

get_provider(id)  →  _REGISTRY.get(id, _REGISTRY["gemini"])
registered_provider_ids()  →  list(_REGISTRY.keys())
```

Adding a new provider requires only two lines in `__init__.py`. The server's queue dict and worker threads are created automatically.

### Prompt Pipeline (`prompt_pipeline.py`)

`build_prompts()` is pure domain logic — no API calls, no side effects. It:
1. Splits the master resume at `\begin{document}` → `preamble` (kept separately for prepending) + `body`
2. Loads `prompts/system_prompt.txt`
3. Conditionally appends `prompts/user_constraints.txt` (wrapped in `<constraints>` tags already)
4. Conditionally appends `prompts/additional_projects.txt` (wrapped in `<project_bank>` tags already)
5. Conditionally appends `prompts/experience_bank.txt` (wrapped in `<experience_bank>` tags already)
6. Assembles user prompt with XML tags: `<resume_body>`, `<job_description>`, `<task>`

Prompt ordering is deliberate: resume body first (lower recency attention), JD second (higher recency), task instruction last (maximum recency attention).

`postprocess_latex()` cleans LLM output:
- Strips markdown fences (` ```latex ... ``` `)
- Converts stray `**text**` to `\textbf{text}`
- Removes blank lines before `\resumeItem` (prevent LaTeX paragraph breaks)

### TeX Parser (`tex_parser.py`)

Two public functions:

`parse_resume_tex(tex)` — used by `/details` endpoint. Returns `{"experience": [...], "projects": [...]}` with structured fields. Parses `\resumeSubheading` (4 args: company+tech, dates, role, location) and `\resumeProjectHeading` (2 args: name+tech, date) macros using brace-depth tracking (not regex) to handle nested `{...}` correctly.

`format_resume_for_eval(tex)` — used by the ClaudeCLI provider after generation to write `output/extras/{company}_Resume.txt`. Returns plain text with `=== EXPERIENCE ===`, `=== PROJECTS ===`, `=== EDUCATION ===`, `=== TECHNICAL SKILLS ===` sections. All LaTeX macros are stripped by `clean_latex()` which iteratively unwraps `\textbf`, `\footnotesize`, `\textit`, etc.

---

## Prompt System (`prompts/`)

### File Roles

| File | Mutability | Purpose |
|------|-----------|---------|
| `system_prompt.txt` | Mutable by optimizer | Core generation rules — whitelist, bullet rules, keyword rules, realism rules |
| `user_constraints.txt` | **Immutable** | Personal hard rules (locked AWS section, GPU model, no cloud projects) |
| `additional_projects.txt` | Manual | Project bank the AI can swap into the resume |
| `experience_bank.txt` | Manual | Experience bank the AI can draw from when tailoring experience sections |
| `prompt_summary.txt` | Synced by optimizer | Compressed rule reference for the evaluator |
| `evaluator_prompt.txt` | Manual | Evaluator role, scoring rubric, output schema |
| `optimizer_prompt.txt` | Manual | Optimizer logic, decision rules, output schema |
| `daily_feedback.json` | Cleared each run | Accumulates evaluation results between optimizer runs |
| `change_tracker.json` | Updated each run | Scoring ledger and rule history |

### Generation Rules (system_prompt.txt)

The generation prompt instructs the AI across five rule groups:

**Priority Order (two-step cascade):**
- Step 1: Find matching content in master resume or project bank → modify tech stack + bullets together
- Step 2: Only if Step 1 fails → invent a realistic, locally-runnable scenario

**Tech Stack Rules:** `\footnotesize{...}` only, max 5 items (experience), max 8 items (projects), always bold with `\textbf{}`, must be consistent with bullets

**Bullet Point Rules:** Four-component architecture (Action Verb → Specific Action → Technical Environment → Quantifiable Outcome), banned vocabulary list (AI-cliche words), structure variation requirements, 60–180 character target per bullet, no filler quantifiers, no responsibility language

**Keyword Rules:** JD coverage, domain targeting, top-third signal (most recent role gets highest keyword density), max 2 sections per keyword, single-tech vs multi-tech distribution rules, selective bolding (one term per bullet)

**Realism Rules:** No 100% accuracy claims, no uptime percentages for local projects, no improvements above 50% without justification, metric defensibility (measurement method must be stateable), intern scope constraints, interview defensibility

**Formatting Constraints:** Exactly 3 experience + 2 project sections, specific bullet count distribution (2 sections with 3 bullets, 3 sections with 4 bullets), 4 skill category headings, one page

---

## Feedback Loop System

```
generate resume
      │
      ▼
/judge-resume {NAME}
  Reads: output/extras/{NAME}_Resume.txt
         output/extras/{NAME}_jd.txt
         prompts/prompt_summary.txt
         prompts/evaluator_prompt.txt
  Appends to: prompts/daily_feedback.json
      │
      ▼  (after N ≥ 5 resumes evaluated)
/optimize-prompt
  Reads: system_prompt.txt, user_constraints.txt,
         change_tracker.json, daily_feedback.json,
         optimizer_prompt.txt, prompt_summary.txt
  Writes: system_prompt.txt (if PROMPT_MODIFIED)
          change_tracker.json (always)
          prompt_summary.txt (if rules changed)
  Clears: daily_feedback.json → []
```

### Evaluator Categories and Weights

| Category | Weight | What It Checks |
|----------|--------|----------------|
| KEYWORD_ALIGNMENT | 30% | JD coverage, distribution, domain targeting, no stuffing |
| BULLET_ARCHITECTURE | 40% | Four-component structure, verb quality, banned vocab, structure variation |
| BELIEVABILITY | 20% | Metric defensibility, intern scope, skill-to-bullet consistency |
| ACCOUNTABILITY | 10% | Personal ownership, no team attribution, project motivation framing |

Pass threshold: `total_score >= 72`

### Optimizer Logic (change_tracker.json)

The optimizer uses a scoring ledger with three threshold actions per rule:

- **TRACKING** → **GRADUATED** (cumulative score ≥ 70): rule is permanently locked
- **TRACKING** → **GRAVEYARD/OBSERVING** (cumulative score ≤ -50): rule is removed; `NEEDS_REPLACEMENT` triggers a rewritten version, `WRONG_PREMISE` removes without replacement
- **GRAVEYARD/OBSERVING** → **READD_PENDING** (absence score ≥ 60): rule re-added if its removal causes measurable quality degradation

Score formula per run: `net_delta = (+15 × pass_rate) + (-20 × fail_rate)`

The sample gate (`minimum_sample_size = 5`) prevents action on insufficient data. Maximum 2 new rules per run to control noise. Graduated rules are permanent and cannot be reverted.

---

## Claude Code Slash Commands (`.claude/commands/`)

### `/tailor-resume <NAME>`

Invoked by `make claude NAME=Company` or by `ClaudeCliProvider` via `claude -p /tailor-resume {name}`.

The command reads the resume, JD, and prompt files, generates a tailored LaTeX body following all rules in `system_prompt.txt`, and writes `output/{NAME}_Resume.tex`. The backend (or Makefile) then compiles it.

Key invariant: the command writes **only** the `.tex` file. Compilation, extras writing, and PDF opening are handled by the caller.

### `/judge-resume <NAME>`

Evaluates a generated resume against the job description snapshot. Outputs only `{"total_score": N}` to stdout and appends the full evaluation JSON to `prompts/daily_feedback.json`.

### `/optimize-prompt`

Runs the full optimizer loop. Reads `daily_feedback.json`, applies the optimizer prompt logic, and updates `system_prompt.txt`, `change_tracker.json`, and `prompt_summary.txt`. Clears `daily_feedback.json` unconditionally.

---

## Testing (`backend/tests/`)

Tests run with pytest from the `backend/` directory. No real API keys or CLI binaries are required — all external calls are mocked.

### Test Files

| File | What It Tests |
|------|--------------|
| `test_tex_parser.py` | `parse_resume_tex()`, `format_resume_for_eval()`, and `clean_latex()` — parsing correctness, edge cases, LaTeX stripping, plain-text eval output |
| `test_prompt_pipeline.py` | `build_prompts()` — preamble splitting, prompt assembly, conditional file loading, postprocessing functions |
| `test_providers.py` | Registry, `ModelConfig`, `GeminiProvider` call chain (waterfall fallback, system_instruction branching), `ClaudeCliProvider` subprocess handling |
| `test_compiler.py` | `find_pdflatex()` discovery chain, `compile_latex()` subprocess behaviour (non-zero exit, FileNotFoundError, aux cleanup, OSError recovery) |
| `test_server.py` | All FastAPI endpoints — validation, capacity limits, company-name fallback paths, job lifecycle, recompile mtime check, file deletion, `_replace_location`, full generate→completed thread-synchronized flow |
| `conftest.py` | Auto-applied fixture: patches `build_prompts`, `get_provider`, and `os.startfile` so worker threads never make real API calls or open files |

**Frontend tests:** The Chrome extension (`popup.js`) is tightly coupled to `chrome.*` APIs and the DOM. A Jest + `jest-chrome` mock setup would be required — deferred until that infrastructure is established.

### Testing Patterns

**Worker thread synchronization:** `test_full_generate_flow_*` tests use `threading.Event` to wait for the background worker to process the job before asserting status. A polling loop (up to 2 seconds) handles the `generate()` return → status update race.

**In-memory job isolation:** The `clear_jobs` fixture in `test_server.py` waits up to 2 seconds for in-flight jobs to reach a terminal state before clearing the `jobs` dict, preventing `KeyError` crashes in worker threads that update jobs after the fixture clears them.

**Provider mock stacking:** `conftest.py` applies a baseline mock to all tests. Flow tests stack their own `patch` context managers on top; Python's mock stacking ensures the innermost patch wins, so flow tests see their own tailored provider behavior.

**No integration tests against real Gemini or Claude:** All AI calls are mocked at the provider level. The test suite verifies orchestration correctness, not AI output quality.

---

## Alternatives Considered

### Per-job threads vs. per-method queues
Rejected: per-job threads would allow unlimited parallel Gemini calls, hitting API rate limits immediately. Per-method queues serialize same-method jobs (correct for rate limits) while still allowing Gemini + Claude to run in parallel.

### SSE vs. WebSocket for log streaming
SSE was chosen because it is unidirectional (server → client only), requires no handshake or keep-alive protocol, works through standard HTTP CORS, and Chrome's `EventSource` API handles reconnection automatically.

### Persistent job store (database) vs. in-memory dict
Deferred: for the current single-user local use case, in-memory is sufficient. The output browser (`/output/resumes`) and company-name fallback pattern on all endpoints mean that even after a server restart, all completed resume files are still accessible. A database would be needed if the server handled multiple users or required history across restarts.

### Full LaTeX re-generation vs. section-level edits
The system sends the full resume body to the LLM and replaces the entire body in one shot. Section-level diffing was considered but rejected: LaTeX section coupling (page budget, skill consistency across sections) makes isolated edits unreliable. Full-body generation lets the LLM reason about page fit holistically.

### Retry persistence: server-side file vs. extension storage vs. hybrid

For storing generation inputs to support the Retry button, three options were considered:

- **Server-side `output/job_details/{company}.json`** *(chosen)*: persists across browser
  restarts, extension reinstalls, and `chrome.storage.local` clears. Consistent with how other
  output files already live in `output/`. One extra HTTP round-trip on retry is negligible.

- **Extension `chrome.storage.local` only**: no file I/O, no round-trip, instant retry. Lost
  if extension is uninstalled or storage cleared. Rejected because server-side durability is
  better for a tool where the user may switch browsers or reinstall the extension.

- **Hybrid (both)**: redundant for a single-user local tool. Rejected as over-engineered.
