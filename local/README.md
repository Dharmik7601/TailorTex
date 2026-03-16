# Local CLI Usage

All commands run from the project root.

## Requirements

- Python 3.x — `pip install -r requirements.txt`
- `pdflatex` on PATH — [MiKTeX](https://miktex.org/) (Windows) or TeX Live (Mac/Linux)
- `.env` file in the project root:
  ```env
  GEMINI_API_KEY=your_api_key_here
  BACKUP_LOCATION=C:\Path\To\Your\Backup\Folder
  ```

## Commands

**Generate + compile:**
```bash
make run NAME=TargetCompany
```

**Disable optional prompt injections:**
```bash
make run NAME=TargetCompany CONSTRAINTS=false PROJECTS=false
```

**Re-compile an existing `.tex` without a new API call:**
```bash
make compile TEX_FILE="output/TargetCompany_Resume.tex" NAME="Final_Draft"
```

**Backup generated files to `BACKUP_LOCATION`:**
```bash
make backup
```
Copies `.pdf` and `.tex` files to `BACKUP_LOCATION/{Company}/`, injecting today's date into the filename (e.g., `TargetCompany_16thMarch2026_Resume.pdf`).

**Generate via Claude Code slash command:**
```
/tailor-resume TargetCompany
```
Reads `job_description.txt`, generates and compiles `output/TargetCompany_Resume.pdf`.

## Inputs

- **Resume**: `master_resume.tex` (root, legacy) or any `.tex` file in `resumes/`
- **Job description**: `job_description.txt`
- **Prompts**: see `prompts/` directory in the project root README
