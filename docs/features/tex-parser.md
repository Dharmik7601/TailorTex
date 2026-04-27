# Feature: TeX Parser

## Files Involved

| File | Role |
|------|------|
| `backend/core/tex_parser.py` | Full implementation |
| `backend/tests/test_tex_parser.py` | Unit tests (parses `resumes/master_resume.tex`) |
| `backend/api/server.py` | Calls `parse_resume_tex()` in the `/details` endpoint |
| `backend/core/providers/claude_cli.py` | Calls `format_resume_for_eval()` to write `output/extras/` files |

---

## Purpose

`tex_parser.py` converts a generated LaTeX resume into two forms:
1. **Structured data** (`parse_resume_tex`) — used by the `/details` endpoint and the extension's View Details panel
2. **Plain text** (`format_resume_for_eval`) — written to `output/extras/{Company}_Resume.txt` for evaluation by `/judge-resume`

All LaTeX macros are stripped from output so consumers receive clean, human-readable text.

---

## Why Brace-Depth Tracking Instead of Regex

LaTeX macro arguments are nested: `\textbf{\footnotesize{Python, Go}}`. A regex like `\textbf{([^}]+)}` would stop at the first `}` inside the nested argument and return `\footnotesize{Python, Go` — incomplete.

`extract_brace_arg(text, pos)` tracks depth instead:

```python
def extract_brace_arg(text: str, pos: int) -> tuple[str, int]:
    depth = 1
    i = pos + 1
    while i < len(text) and depth > 0:
        if text[i] == "{": depth += 1
        elif text[i] == "}": depth -= 1
        i += 1
    return (text[pos + 1 : i - 1], i)  # content between the matched braces
```

Starting from the opening `{`, it increments depth on `{` and decrements on `}`. When depth reaches 0, the matching closing brace has been found. This handles arbitrary nesting depth correctly.

---

## `clean_latex()` — LaTeX Stripping Pipeline

Used on every field extracted from the resume before returning it to callers.

### Step 1: Iteratively Unwrap Single-Arg Commands

```python
for cmd in (r"\\textbf", r"\\footnotesize", r"\\textit", r"\\underline", r"\\emph", r"\\small"):
    # find \cmd{...} → replace with inner content, repeat until stable
```

Uses `extract_brace_arg()` to correctly handle nested commands. The `while changed` loop repeats until no more matches are found — necessary for deeply nested cases like `\textbf{\footnotesize{text}}`.

### Step 2: Replace Known LaTeX Symbols

```python
text.replace(r"\textbar{}", "|")    # pipe character
text.replace(r"\&", "&")
text.replace(r"\%", "%")
text.replace(r"\$", "$")
text.replace(r"\#", "#")
text.replace(r"\_", "_")
text.replace("$|$", "|")            # math-mode pipe
```

### Step 3: Remove Structural Commands

```python
re.sub(r"\\vspace\{[^}]*\}", "", text)  # vertical spacing
text.replace("\\\\", "")               # line break command
re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)  # href{url}{text} → text
```

### Step 4: Normalize Whitespace

Collapses multiple spaces/tabs to one, strips leading/trailing whitespace.

---

## `parse_resume_tex()` — Structured Output

### High-Level Flow

```python
def parse_resume_tex(tex_content: str) -> dict:
    experience_section = _extract_section(tex_content, "Experience")
    projects_section   = _extract_section(tex_content, "Projects")
    return {
        "experience": _parse_experience(experience_section),
        "projects":   _parse_projects(projects_section),
    }
```

Returns `{"experience": [], "projects": []}` on empty input.

### `_extract_section(tex, section_name)`

Finds `\section{Experience}` (case-insensitive), then returns everything from that point until the next `\section{` or end of document. This isolates each section's content for targeted parsing.

### `_parse_experience(section_text)` — Experience Entry Shape

Finds all `\resumeSubheading` positions using `_find_all_command()`, then extracts 4 consecutive brace arguments per entry:

| Arg # | Content | Example |
|-------|---------|---------|
| 1 | Company + tech stack | `Amazon Web Services (AWS) \textbar{} \footnotesize{\textbf{Bedrock, Lambda, CDK}}` |
| 2 | Dates | `May 2025 - Aug 2025` |
| 3 | Role | `Software Development Engineer Intern` |
| 4 | Location | `East Palo Alto, CA, USA` |

The company line is split on `\textbar{}` (or `\textbar` fallback) to separate `company` from `tech_stack`. Both are passed through `clean_latex()`.

Bullet points are extracted from the block between this entry and the next `\resumeSubheading` (or end of section) using `_extract_bullets()`.

**Output shape per entry:**
```python
{
    "company": "Amazon Web Services (AWS)",
    "tech_stack": "Bedrock, Lambda, CDK",
    "dates": "May 2025 - Aug 2025",
    "role": "Software Development Engineer Intern",
    "location": "East Palo Alto, CA, USA",
    "bullets": ["Built...", "Designed...", ...]
}
```

### `_parse_projects(section_text)` — Project Entry Shape

Finds all `\resumeProjectHeading` positions, extracts 2 brace arguments:

| Arg # | Content | Example |
|-------|---------|---------|
| 1 | Name + tech stack | `\textbf{Distributed File System \textbar{} \footnotesize{\textbf{C++, P2P, AES Encryption}}}` |
| 2 | Date (often empty) | `` |

The cleaned first argument is split on `|` (post-cleaning) to separate name from tech stack.

**Output shape per entry:**
```python
{
    "name": "Distributed File System",
    "tech_stack": "C++, P2P, AES Encryption",
    "bullets": ["Engineered...", "Implemented...", ...]
}
```

### `_extract_bullets(text)`

Uses `re.finditer(r"\\resumeItem\{", text)` to find all bullet positions (specifically `\resumeItem{`, not `\resumeItemListStart` etc.), then calls `extract_brace_arg()` on each to get the full content, passing it through `clean_latex()`.

---

## `format_resume_for_eval()` — Plain-Text Output

Used by the feedback loop. Returns a clean multi-section string suitable for LLM evaluation.

```
=== EXPERIENCE ===
Amazon Web Services (AWS) | Software Development Engineer Intern
Tech Stack: Bedrock, Lambda, CDK
- Built a distributed ingestion layer...
- Designed fault-tolerant transformation...

=== PROJECTS ===
Distributed File System
Tech Stack: C++, P2P, AES Encryption
- Engineered a zero-copy IPC protocol...

=== EDUCATION ===
University of Rochester (Aug 2022 - May 2026): B.S. Computer Science | ...

=== TECHNICAL SKILLS ===
Languages: Python, C++, Go, Java
```

**Section ordering:** Experience → Projects → Education → Skills. Skills are placed last so the evaluator can cross-reference them against the bullets above — a skill listed in Skills but not demonstrated in any bullet is a `BELI_SKILL_TO_BULLET` failure.

---

## Tests (`backend/tests/test_tex_parser.py`)

Tests parse `resumes/master_resume.tex` at module load time (`RESULT = parse_resume_tex(MASTER_TEX)`) — one real parse, all assertions against the same result object. This avoids repeated I/O and keeps tests fast.

### Structure Tests

| Test | What it verifies |
|------|-----------------|
| `test_output_has_experience_and_projects_keys` | Both keys present in result dict |
| `test_experience_is_list` | `experience` value is a list |
| `test_projects_is_list` | `projects` value is a list |
| `test_experience_entry_structure` | Every experience entry has all 6 expected fields |
| `test_project_entry_structure` | Every project entry has all 3 expected fields |
| `test_bullets_are_lists_of_strings` | All bullet lists contain strings only |

### Count Tests

| Test | What it verifies |
|------|-----------------|
| `test_experience_count` | Exactly 3 experience entries parsed |
| `test_project_count` | Exactly 2 project entries parsed |
| `test_experience_bullet_counts` | AWS: 4 bullets, Acute: 3 bullets, WPServiceDesk: 3 bullets |

### Value Spot-Checks

| Test | What it verifies |
|------|-----------------|
| `test_first_experience_company` | `"Amazon Web Services (AWS)"` |
| `test_first_experience_role` | `"Software Development Engineer Intern"` |
| `test_first_experience_location` | `"East Palo Alto, CA, USA"` |
| `test_first_experience_dates` | `"May 2025 - Aug 2025"` |
| `test_first_experience_tech_stack` | Tech stack contains `"Bedrock"`, `"Lambda"`, `"CDK"` |
| `test_second_experience_company` | `"Acute Informatics Pvt. Ltd."` |
| `test_first_project_name` | `"Distributed File System"` |
| `test_second_project_name` | `"Go HTTP Server"` |
| `test_first_project_tech_stack` | Contains `"C++"`, `"P2P"`, `"AES Encryption"` |

### Clean LaTeX Tests

| Test | What it verifies |
|------|-----------------|
| `test_no_latex_commands_in_bullets` | No `\textbf`, `\footnotesize`, or `\resumeItem` in any bullet |
| `test_no_unescaped_latex_chars` | No `\&`, `\%`, `\$`, `\#`, `\_` in any bullet |
| `test_bullet_text_no_braces` | No stray `{` or `}` in any bullet |

### Edge Case Tests

| Test | What it verifies |
|------|-----------------|
| `test_empty_input` | Returns `{"experience": [], "projects": []}` on empty string |
| `test_no_experience_section` | Projects parsed correctly when Experience section absent |
| `test_no_projects_section` | Experience parsed correctly when Projects section absent (followed by `\section{Education}`) |

### `clean_latex()` Direct Unit Tests

| Test | What it verifies |
|------|-----------------|
| `test_clean_latex_removes_textbf` | `\textbf{Python}` → `Python` |
| `test_clean_latex_removes_footnotesize` | `\footnotesize{Go, Python}` → `Go, Python` |
| `test_clean_latex_removes_textit` | `\textit{italic text}` → `italic text` |
| `test_clean_latex_removes_underline` | `\underline{underlined}` → `underlined` |
| `test_clean_latex_unescapes_ampersand` | `\&` → `&` present in output |
| `test_clean_latex_unescapes_percent` | `\%` → `%` present in output |
| `test_clean_latex_replaces_textbar` | `\textbar{}` → `\|` in output, no `\textbar` remaining |
| `test_clean_latex_handles_nested_commands` | `\textbf{\textit{word}}` → `word` (iterative unwrap) |
| `test_clean_latex_removes_href` | `\href{url}{label}` → `label`, URL absent from output |

### `format_resume_for_eval()` Tests

| Test | What it verifies |
|------|-----------------|
| `test_format_eval_has_experience_section_header` | `=== EXPERIENCE ===` present in output |
| `test_format_eval_has_projects_section_header` | `=== PROJECTS ===` present in output |
| `test_format_eval_no_latex_commands_in_output` | No `\textbf`, `\resumeItem`, `\footnotesize` in output |
| `test_format_eval_empty_input_returns_empty_string` | Returns `""` for empty input |
