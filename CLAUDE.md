# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

TailorTex generates customized LaTeX resumes tailored to a specific job description and compiles them to PDF via `pdflatex`. It has two modes of operation:

1. **CLI** — directly via `make run` (legacy, Gemini API only)
2. **Extension + API** — a Chrome side-panel extension that talks to a local FastAPI backend, supporting both Gemini and Claude Code as generation methods, with a multi-job queue

---

## Project Structure

```
TailorTex/
├── backend/
│   ├── api/
│   │   ├── server.py          # FastAPI app — all endpoints
│   │   └── schemas.py         # Pydantic models
│   └── core/
│       ├── prompt_pipeline.py # Preamble split, prompt file loading, LaTeX post-processing (shared across providers)
│       ├── compiler.py        # Standalone LaTeX compiler
│       ├── tex_parser.py      # Parse generated .tex into structured data + plain-text for eval
│       └── providers/
│           ├── __init__.py    # Provider registry: get_provider(), registered_provider_ids()
│           ├── base.py        # ResumeProvider ABC + GenerationRequest/GenerationResult dataclasses
│           ├── registry.py    # ModelConfig dataclass + GEMINI_MODEL_CHAIN (declarative model config)
│           ├── gemini.py      # GeminiProvider — waterfall fallback across GEMINI_MODEL_CHAIN
│           └── claude_cli.py  # ClaudeCliProvider — subprocess-based via claude -p /tailor-resume
├── frontend/
│   ├── extension/             # Chrome MV3 side-panel extension
│   │   ├── manifest.json
│   │   ├── background.js      # Opens side panel on action click
│   │   ├── popup.html         # Always-visible form + queue panel
│   │   ├── popup.js           # All extension logic
│   │   └── popup.css          # Dark theme styles
│   └── src/                   # React frontend (Vite) — run via `make serve-ui`
│       ├── App.jsx
│       ├── main.jsx
│       └── components/
│           ├── LogViewer.jsx
│           ├── ResumeForm.jsx
│           └── DownloadButton.jsx
├── local/
│   ├── main.py                # CLI entry point (Gemini API, argparse)
│   ├── compile.py             # Standalone LaTeX compiler script
│   └── backup.py              # Backup output PDFs to BACKUP_LOCATION
├── prompts/
│   ├── system_prompt.txt      # Core LLM rules (whitelist of editable sections)
│   ├── user_constraints.txt   # Per-run hard constraints (immutable by optimizer)
│   ├── additional_projects.txt# Project bank for swapping into resume
│   ├── prompt_summary.txt     # Compressed rule reference used by the evaluator
│   ├── evaluator_prompt.txt   # Evaluator role, scoring categories, output schema
│   ├── optimizer_prompt.txt   # Optimizer rules, decision constraints, output schema
│   ├── daily_feedback.json    # Accumulated evaluation results (cleared after /optimize-prompt)
│   └── change_tracker.json    # Scoring ledger and rule history for the optimizer
├── resumes/                   # Base .tex resume files selectable in the extension
├── output/
│   ├── *.tex / *.pdf          # Generated resumes (gitignored)
│   └── extras/                # Per-job evaluation artifacts (plain-text resume + JD snapshot)
├── job_description.txt        # Used by CLI and Claude Code slash command
├── Makefile
└── requirements.txt
```

---

## Running the Backend

```bash
cd backend
uvicorn api.server:app --port 8001 --reload
```

The backend must be running at `http://localhost:8001` for the extension to work.

---

## Loading the Extension

1. Go to `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** → select `frontend/extension/`
4. The extension opens as a **side panel** (not a popup)

After any code change to the extension files, click the **reload icon** on the extension card in `chrome://extensions`, then close and reopen the side panel.

---

## CLI Commands

```bash
pip install -r requirements.txt

make run NAME=TargetCompany                          # generate + compile (Gemini)
make run NAME=TargetCompany CONSTRAINTS=false PROJECTS=false
make claude NAME=TargetCompany                       # generate via Claude Code CLI
make compile NAME=TargetCompany                      # re-compile existing .tex
make backup                                          # backup output/ to BACKUP_LOCATION
make clean                                           # clear output/ and output/extras/
make setup                                           # create venv + install requirements
make serve-api                                       # run FastAPI backend (port 8001)
make serve-ui                                        # run React frontend (Vite)
make dev                                             # run both servers in parallel
```

The `make run` command calls `local/main.py` directly with argparse flags (`--jd`, `--output`, `--constraints`, `--projects`).

---

## Environment Setup

`.env` file in the root:
```env
GEMINI_API_KEY=your_api_key_here
BACKUP_LOCATION=C:\Path\To\Your\Backup\Folder
```

`pdflatex` must be installed and on PATH (MiKTeX on Windows, TeX Live on Mac/Linux).

---

## Backend API — Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/resumes` | List `.tex` files in `resumes/` |
| POST | `/generate` | Submit a job (form fields below) |
| GET | `/queue` | All jobs currently in memory |
| GET | `/status/{job_id}` | SSE stream of log lines + completion event |
| GET | `/status/{job_id}/json` | Snapshot status (non-streaming) |
| GET | `/open/{job_id}?company=X` | Open the PDF with the system default viewer |
| GET | `/download/{job_id}` | Serve the PDF as a file download |
| GET | `/details/{job_id}?company=X` | Return parsed Experience and Projects from the generated .tex |

### POST `/generate` form fields
| Field | Type | Description |
|-------|------|-------------|
| `company_name` | string | Used in output filename |
| `job_description` | string | Full JD text |
| `resume_name` | string | e.g. `resumes/master_resume.tex` |
| `resume_file` | file | Alternative to resume_name |
| `method` | string | `"gemini"` or `"claudecli"` |
| `use_constraints` | bool | Append user_constraints.txt to prompt |
| `use_projects` | bool | Append additional_projects.txt to prompt |

Returns `{"job_id": "<uuid>"}`. Max 5 active jobs (queued + running); returns HTTP 429 if full.

---

## Job Queue Architecture

### Per-method worker queues
Each AI method has its own `queue.Queue` and a single dedicated daemon worker thread. The queue dict is **derived from the provider registry** — registering a new provider automatically creates its queue and worker thread with no changes to `server.py`.

```
_work_queues = {pid: queue.Queue() for pid in registered_provider_ids()}
# Currently yields: {"gemini": Queue(), "claudecli": Queue()}
```

**Behaviour:**
- Gemini + Claude jobs run **in parallel** (separate threads)
- Two Gemini jobs run **sequentially** (one worker, one at a time)
- Two Claude jobs run **sequentially** (one worker, one at a time)
- No threads ever block spinning — workers sleep on `queue.get()`

### Job lifecycle
`queued` → (picked up by worker) → `running` → `completed` | `error`

On **completion**: backend automatically calls `os.startfile(pdf_path)` to open the PDF in the system default viewer.

On **error**: full Python traceback is written to the job's log list (visible in the extension Logs panel).

### In-memory only
The `jobs` dict is in-memory and lost on server restart. The `/open` endpoint handles this with a fallback: if the job_id is not in memory, it reconstructs the path as `output/{company}_Resume.pdf` using the `company` query param passed by the extension.

---

## Backend Provider Architecture

The backend uses a **Strategy + Registry** pattern to keep AI providers cleanly separated and independently extensible.

### Layers

| Layer | File | Responsibility |
|---|---|---|
| **Prompt pipeline** | `core/prompt_pipeline.py` | Preamble splitting, prompt file loading, LaTeX post-processing. Shared by all providers. |
| **Provider interface** | `core/providers/base.py` | `ResumeProvider` ABC, `GenerationRequest`, `GenerationResult` |
| **Model config** | `core/providers/registry.py` | `ModelConfig` dataclass + `GEMINI_MODEL_CHAIN` list (declarative, replaces if/else branching) |
| **Gemini provider** | `core/providers/gemini.py` | Iterates `GEMINI_MODEL_CHAIN` with waterfall fallback; handles `supports_system_instruction` per model |
| **Claude CLI provider** | `core/providers/claude_cli.py` | Writes JD to disk, runs `claude -p /tailor-resume`, compiles result |
| **Registry** | `core/providers/__init__.py` | `get_provider(id)`, `registered_provider_ids()` |

### Adding a new AI provider

1. Create `backend/core/providers/<name>.py` — implement `ResumeProvider` (define `provider_id` and `generate()`)
2. In `core/providers/__init__.py`, add two lines:
   ```python
   from core.providers.<name> import MyProvider
   _register(MyProvider())
   ```
3. Done — queue worker is created automatically, `server.py` needs no changes

### Adding a new Google model

Edit `GEMINI_MODEL_CHAIN` in `core/providers/registry.py`:
```python
ModelConfig(name="gemini-2-pro", supports_system_instruction=True, temperature=0.1)
```
Models are tried in order; first success wins.

---

## Extension Architecture (`frontend/extension/`)

### State management
Job state is stored in `chrome.storage.local` under key `ttjobs` — an array of job objects:

```json
{
  "ttjobs": [
    {
      "job_id": "uuid",
      "company": "Google",
      "resume_name": "resumes/master_resume.tex",
      "method": "gemini",
      "status": "queued",
      "submitted_at": 1234567890,
      "log": []
    }
  ]
}
```

### Cross-tab sync
`chrome.storage.onChanged` listener re-renders the queue panel whenever any tab updates storage. All open side panels stay in sync automatically.

### SSE connections
Each active job gets an `EventSource` connection to `/status/{job_id}`. Connections are tracked in a module-level `Map` (`sseMap`) to prevent duplicates. On `completed`/`error` SSE events, the job status is updated in storage and the SSE is closed.

### Key functions in popup.js
| Function | Purpose |
|----------|---------|
| `generate()` | Submits form to `/generate`, pushes job to storage |
| `renderQueue(jobs)` | Rebuilds job card DOM from jobs array |
| `attachSSE(job_id)` | Opens SSE stream, updates log + status in storage |
| `getJobs()` / `saveJobs()` | Read/write `chrome.storage.local` |
| `updateJobStatus()` | Patch a single job's fields in storage |
| `removeJob()` | Remove a job from storage (discard button) |

### Queue UI behaviour
- Form stays visible at all times (no more single-job status screen)
- Slot counter shows `N / 5 slots used`
- Generate button disabled when 5 slots are full or API is offline
- Each job card shows: company, resume file, method badge, status badge, collapsible logs, Open PDF button (completed only), X discard button
- **Open PDF** calls `GET /open/{job_id}?company={company}` — server opens file locally, nothing sent back to extension

---

## Claude Code Slash Commands

### `/tailor-resume <NAME>`
Generates `output/<NAME>_Resume.tex` from `job_description.txt` following all rules in `prompts/system_prompt.txt`. **The command only writes the `.tex` file** — the backend handles compilation, cleanup, and opening the PDF.

Steps:
1. Read `resumes/master_resume.tex` — split at `\begin{document}`, keep preamble separate
2. Read `job_description.txt`
3. Read `prompts/system_prompt.txt`, `prompts/user_constraints.txt` (if non-empty), `prompts/additional_projects.txt` (if non-empty)
4. Generate tailored body applying all rules; `system_prompt.txt` formatting constraints guarantee one-page fit
5. Reassemble full `.tex` (preamble + body) and write to `output/<NAME>_Resume.tex`

### `/judge-resume <NAME>`
Evaluates the generated resume at `output/extras/<NAME>_Resume.txt` against the job description snapshot at `output/extras/<NAME>_jd.txt`.

Steps:
1. Read `output/extras/<NAME>_Resume.txt` — plain-text resume extract written by the backend
2. Read `output/extras/<NAME>_jd.txt` — JD snapshot written by the backend for this job
3. Read `prompts/prompt_summary.txt` and `prompts/evaluator_prompt.txt`
4. Apply the evaluator rubric; append result to `prompts/daily_feedback.json`
5. Output only `{"total_score": <number>}` to stdout

### `/optimize-prompt`
Analyzes daily evaluation feedback and updates `prompts/system_prompt.txt`, `prompts/change_tracker.json`, and `prompts/prompt_summary.txt`.

Steps:
1. Read `prompts/system_prompt.txt`, `prompts/user_constraints.txt`, `prompts/change_tracker.json`, `prompts/daily_feedback.json`, `prompts/optimizer_prompt.txt`, `prompts/prompt_summary.txt`
2. Apply optimizer logic (sample gate, aggregate feedback, score rules, decide changes)
3. Overwrite `prompts/system_prompt.txt` only if `action_taken == PROMPT_MODIFIED`
4. Always overwrite `prompts/change_tracker.json`
5. Update `prompts/prompt_summary.txt` if rules changed
6. Clear `prompts/daily_feedback.json` to `[]` unconditionally

## Resume Generation Rules (for Claude Code)

1. Read `resumes/master_resume.tex` and split at `\begin{document}` — send only the body
2. Read `job_description.txt`
3. Read `prompts/system_prompt.txt`
4. Optionally read `prompts/user_constraints.txt` and `prompts/additional_projects.txt` if non-empty
5. Only modify content inside `\footnotesize{...}`, `\resumeItem{...}`, and `\textbf{...}` macros — do NOT change structure or preamble
6. Guarantee one page
7. Reassemble full `.tex` with original preamble prepended
8. Write to `output/<NAME>_Resume.tex` (backend handles compilation)

---

## Key Prompt Files

| File | Purpose |
|------|---------|
| `prompts/system_prompt.txt` | Core AI rules — whitelist of editable sections, one-page guarantee |
| `prompts/user_constraints.txt` | Per-run hard rules (immutable — never modified by optimizer) |
| `prompts/additional_projects.txt` | Project bank the AI can swap in |
| `prompts/prompt_summary.txt` | Compressed rule reference used by the evaluator; kept in sync with system_prompt |
| `prompts/evaluator_prompt.txt` | Evaluator role, scoring categories, and output schema for `/judge-resume` |
| `prompts/optimizer_prompt.txt` | Optimizer rules, decision constraints, and output schema for `/optimize-prompt` |
| `prompts/daily_feedback.json` | Array of evaluation results; consumed and cleared by `/optimize-prompt` |
| `prompts/change_tracker.json` | Scoring ledger and rule history; updated by every `/optimize-prompt` run |

**If you change the LaTeX template structure in `master_resume.tex`, update the whitelist in `prompts/system_prompt.txt`.**

---

## Feedback Loop System

The project has an automated prompt optimization loop:

```
generate resume → /judge-resume → daily_feedback.json → /optimize-prompt → system_prompt.txt
```

1. **Generation**: `/tailor-resume <NAME>` writes `output/<NAME>_Resume.tex`; the backend also writes `output/extras/<NAME>_Resume.txt` (plain-text extract) and `output/extras/<NAME>_jd.txt` (JD snapshot)
2. **Evaluation**: `/judge-resume <NAME>` scores the resume and appends to `prompts/daily_feedback.json`
3. **Optimization**: `/optimize-prompt` reads `daily_feedback.json`, applies optimizer logic, and updates `system_prompt.txt` + `change_tracker.json` + `prompt_summary.txt`; then clears `daily_feedback.json`

### `tex_parser.py` — Key functions
| Function | Purpose |
|----------|---------|
| `parse_resume_tex(tex)` | Returns `{"experience": [...], "projects": [...]}` (used by `/details` endpoint) |
| `format_resume_for_eval(tex)` | Returns clean plain-text (Experience, Projects, Education, Skills) for LLM evaluation |

---

## Output Conventions

- Output files: `output/{NAME}_Resume.tex` and `output/{NAME}_Resume.pdf`
- Backup filenames inject date: `{Company}_{9thMarch2026}_Resume.pdf`
- Company name for backup: parsed from filename prefix (text before first `-` or `_`)
