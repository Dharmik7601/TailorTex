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
│       ├── generator.py       # Gemini API call + pdflatex compile
│       └── compiler.py        # Standalone LaTeX compiler
├── frontend/
│   └── extension/             # Chrome MV3 side-panel extension
│       ├── manifest.json
│       ├── background.js      # Opens side panel on action click
│       ├── popup.html         # Always-visible form + queue panel
│       ├── popup.js           # All extension logic
│       └── popup.css          # Dark theme styles
├── prompts/
│   ├── system_prompt.txt      # Core LLM rules (whitelist of editable sections)
│   ├── user_constraints.txt   # Per-run hard constraints
│   └── additional_projects.txt# Project bank for swapping into resume
├── resumes/                   # Base .tex resume files selectable in the extension
├── output/                    # Generated .tex and .pdf files (gitignored)
├── master_resume.tex          # Root-level master resume (legacy CLI path)
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

## CLI Commands (legacy)

```bash
pip install -r requirements.txt

make run NAME=TargetCompany                          # generate + compile
make run NAME=TargetCompany CONSTRAINTS=false PROJECTS=false
make compile NAME=TargetCompany                      # re-compile existing .tex
make backup                                          # backup output/ to BACKUP_LOCATION
make clean                                           # clear output/
```

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
Each AI method has its own `queue.Queue` and a single dedicated daemon worker thread:

```
_work_queues = {
    "gemini":    queue.Queue(),   →  thread: worker-gemini
    "claudecli": queue.Queue(),   →  thread: worker-claudecli
}
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

## How to Generate a Resume with Claude Code (slash command)

```
/tailor-resume <NAME>
```

This reads `job_description.txt` and generates `output/<NAME>_Resume.tex` + PDF following all rules in `prompts/system_prompt.txt`.

## Resume Generation Rules (for Claude Code)

1. Read `master_resume.tex` (or `resumes/master_resume.tex`) and split at `\begin{document}` — send only the body
2. Read `job_description.txt`
3. Read `prompts/system_prompt.txt`
4. Optionally read `prompts/user_constraints.txt` and `prompts/additional_projects.txt` if non-empty
5. Only modify content inside `\footnotesize{...}`, `\resumeItem{...}`, and `\textbf{...}` macros — do NOT change structure or preamble
6. Guarantee one page
7. Reassemble full `.tex` with original preamble prepended
8. Write to `output/<NAME>_Resume.tex`
9. Compile: `pdflatex -interaction=nonstopmode -output-directory=output output/<NAME>_Resume.tex`
10. Delete aux files: `.aux`, `.log`, `.out`
11. Open PDF: `start output/<NAME>_Resume.pdf`

---

## Key Prompt Files

| File | Purpose |
|------|---------|
| `prompts/system_prompt.txt` | Core AI rules — whitelist of editable sections, one-page guarantee |
| `prompts/user_constraints.txt` | Per-run hard rules |
| `prompts/additional_projects.txt` | Project bank the AI can swap in |

**If you change the LaTeX template structure in `master_resume.tex`, update the whitelist in `prompts/system_prompt.txt`.**

---

## Output Conventions

- Output files: `output/{NAME}_Resume.tex` and `output/{NAME}_Resume.pdf`
- Backup filenames inject date: `{Company}_{9thMarch2026}_Resume.pdf`
- Company name for backup: parsed from filename prefix (text before first `-` or `_`)
