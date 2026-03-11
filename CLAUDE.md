# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

TailorTex is a CLI Python tool that generates a customized LaTeX resume tailored to a specific job description, then compiles it to PDF via `pdflatex`.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Full pipeline: generate tailored resume + compile PDF
make run NAME=TargetCompany

# Disable optional prompt injections
make run NAME=TargetCompany CONSTRAINTS=false PROJECTS=false

# Manually re-compile a .tex file without an API call
make compile NAME=TargetCompany

# Backup output/ PDFs and .tex files to BACKUP_LOCATION (from .env)
make backup

# Clear output/ directory and empty job_description.txt
make clean
```

## Environment Setup

Create a `.env` file in the root:
```env
GEMINI_API_KEY=your_api_key_here
BACKUP_LOCATION=C:\Path\To\Your\Backup\Folder
```

`pdflatex` must be installed and on PATH (MiKTeX on Windows, TeX Live on Mac/Linux).

## How to Generate a Resume with Claude Code

Claude Code can run this pipeline directly without using the Gemini API. Use the custom slash command:

```
/tailor-resume <NAME>
```

**Example:**
```
/tailor-resume Google_SWE
```

This will:
1. Read `Master_Resume.tex`, `job_description-claude.txt`, and all prompt files
2. Generate a tailored LaTeX resume body following all rules in `prompts/system_prompt.txt`
3. Reassemble the full `.tex` (preamble + tailored body)
4. Save to `output/<NAME>_Resume.tex`
5. Compile to `output/<NAME>_Resume.pdf` via `pdflatex`

**Before running**, make sure:
- `job_description-claude.txt` contains the target job posting
- `NAME` is the company or role identifier you want in the filename

**Non-interactive one-liner** (useful for scripting):
```bash
claude -p "Tailor the resume for the job in job_description-claude.txt. Follow all rules in prompts/system_prompt.txt, optionally use prompts/user_constraints.txt and prompts/additional_projects.txt. Save output as output/Google_SWE_Resume.tex and compile it with pdflatex."
```

## Resume Generation Rules (for Claude Code)

When generating a tailored resume, always:

1. Read `Master_Resume.tex` and split at `\begin{document}` — send only the body to avoid unnecessary preamble changes
2. Read `job_description-claude.txt` for the target role requirements
3. Read `prompts/system_prompt.txt` for the core modification rules (whitelist of editable sections)
4. Optionally read `prompts/user_constraints.txt` and `prompts/additional_projects.txt` if they are non-empty
5. Only modify content inside `\footnotesize{...}`, `\resumeItem{...}`, and `\textbf{...}` macros — do NOT change the LaTeX structure or preamble
6. Guarantee the resume fits one page
7. Reassemble the full `.tex` by prepending the original preamble (everything up to and including `\begin{document}`)
8. Write the complete file to `output/<NAME>_Resume.tex`
9. Compile using: `pdflatex -interaction=nonstopmode -output-directory=output output/<NAME>_Resume.tex`
10. Delete auxiliary files: `output/<NAME>_Resume.aux`, `.log`, `.out`
11. Open the PDF: `start output/<n>_Resume.pdf`

## Architecture

The pipeline has four stages in `main.py`:

1. **Load** — reads `Master_Resume.tex`, `job_description.txt`, and prompt files. Splits the `.tex` at `\begin{document}` to send only the body (not the preamble) to the LLM, saving tokens.
2. **Prompt assembly** — builds the system prompt from `prompts/system_prompt.txt`, optionally appending `prompts/user_constraints.txt` (`--constraints`) and `prompts/additional_projects.txt` (`--projects`).
3. **API call** — calls Gemini with a model fallback list (`gemini-3-flash-preview` → `gemini-2.5-flash`). Temperature is set to `0.2` for deterministic LaTeX structure. Strips markdown fences from the response via regex.
4. **Compile** — runs `pdflatex` via subprocess, saves `.tex` and `.pdf` to `output/`, deletes `.aux`/`.log`/`.out`.

Supporting scripts:
- `compile.py` — standalone LaTeX compiler (used by `make compile`)
- `backup.py` — copies `output/*.pdf` and `output/*.tex` to `BACKUP_LOCATION/<CompanyName>/`, injecting today's date into filenames

## Key Prompt Files

| File | Purpose |
|------|---------|
| `prompts/system_prompt.txt` | Core AI rules — whitelist of what the LLM may modify, formatting constraints, one-page guarantee logic |
| `prompts/user_constraints.txt` | Per-run hard rules (e.g., "don't change X job") |
| `prompts/additional_projects.txt` | Project bank the AI can swap into the resume |

**If you change the LaTeX template structure in `Master_Resume.tex`, you must update the "Content Modification Rules (Whitelist)" section in `prompts/system_prompt.txt`** — the AI's edit permissions are section-specific (`\footnotesize{...}`, `\resumeItem{...}`, `\textbf{...}` macros).

## Output Conventions

- Output files are named `{NAME}_Resume.tex` and `{NAME}_Resume.pdf` in `output/`
- Backup filenames inject the date: `{Company}_{9thMarch2026}_Resume.pdf`
- Company name for backup grouping is parsed from the filename prefix (text before the first `-` or `_`)
