# TailorTex

Rewrites your LaTeX resume to match a job description and compiles it to PDF via `pdflatex`.

![Extension screenshot](assets/Extension_Screenshot.png)

## What it does

- Rewrites bullet points and project descriptions to match the job description
- Only edits whitelisted sections — never touches company names, job titles, or LaTeX structure
- Guarantees one-page output
- Compiles `.tex` → `.pdf` automatically and opens the result
- Runs multiple jobs in parallel (Gemini + Claude in separate queues)

## Requirements

- Python 3.x
- `pdflatex` on PATH — [MiKTeX](https://miktex.org/) (Windows) or TeX Live (Mac/Linux)
- `.env` file in the project root:
  ```
  GEMINI_API_KEY=your_key_here
  BACKUP_LOCATION=C:\Path\To\Backup
  ```

## Setup

**Install dependencies**

```bash
pip install -r requirements.txt
```

**Add your resume**

Copy `examples/resumes/master_resume.tex` to `resumes/` and edit it to be yours.

**Add your prompts** (optional but recommended)

Copy the files from `examples/prompts/` to `prompts/` and edit them:

| File | What to put in it |
|------|------------------|
| `system_prompt.txt` | Core AI rules — update if you change your resume's section structure |
| `user_constraints.txt` | Hard rules per run, e.g. "never change the AWS job title" |
| `additional_projects.txt` | Extra projects the AI can swap in if they fit the JD better |

## Usage — Chrome Extension (recommended)

**Start the backend**

```bash
make serve-api
```

**Load the extension**

1. Go to `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** → select `frontend/extension/`
4. Click the extension icon — it opens as a side panel

Paste a job description, pick a resume and location, choose Gemini or Claude, and click Generate. Logs stream in real time; the PDF opens automatically when done.

## Usage — CLI

```bash
# Gemini
make run NAME=TargetCompany

# Claude Code CLI
make claude NAME=TargetCompany
```

Both read from `job_description.txt` and write `output/TargetCompany_Resume.tex` / `.pdf`.

## Other commands

```bash
make dev        # backend + React UI together
make backup     # copy output/ to BACKUP_LOCATION with date-stamped filenames
make test       # run the backend test suite
```

## Generation methods

| Method | Requires |
|--------|---------|
| `gemini` | `GEMINI_API_KEY` in `.env` |
| `claudecli` | Claude Code CLI (`claude` on PATH) |
