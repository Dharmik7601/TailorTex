# Feature: Prompt Pipeline

## Files Involved

| File | Role |
|------|------|
| `backend/core/prompt_pipeline.py` | Implementation |
| `backend/tests/test_prompt_pipeline.py` | Unit tests |
| `prompts/system_prompt.txt` | Core generation rules loaded at runtime |
| `prompts/user_constraints.txt` | Optional hard constraints appended to system prompt |
| `prompts/additional_projects.txt` | Optional project bank appended to system prompt |

---

## Purpose

`prompt_pipeline.py` is the shared prompt-assembly layer used by every AI provider. It is pure domain logic — no API calls, no file writes, no side effects. Its job is to take a raw `.tex` file and a job description and produce fully assembled system and user prompts ready for any provider to send to an LLM.

---

## Public Interface

```python
# dataclass returned by build_prompts()
@dataclass
class PipelineOutput:
    system_prompt: str          # fully assembled, with constraints + project bank appended
    user_prompt: str            # XML-tagged: resume body + JD + task instruction
    preamble: str               # LaTeX preamble (everything before \begin{document})
    raw_job_description: str    # preserved verbatim for ClaudeCliProvider to write to disk

def build_prompts(
    master_resume_tex: str,
    job_description: str,
    use_constraints: bool = True,
    use_projects: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> PipelineOutput: ...

def postprocess_latex(raw_latex: str) -> str: ...
def validate_latex(latex: str) -> None: ...
```

---

## `build_prompts()` — Step-by-Step

### Step 1: Preamble Split

```python
delimiter = r"\begin{document}"
preamble, body_rest = master_resume_tex.split(delimiter, 1)
resume_body = delimiter + body_rest
```

The master resume is split at `\begin{document}`. The preamble (everything before it) is stored separately and prepended to LLM output before writing to disk — it is never sent to the AI. Only `resume_body` (from `\begin{document}` onwards) travels in the prompt.

Raises `ValueError` if `\begin{document}` is not found.

### Step 2: Load System Prompt

`prompts/system_prompt.txt` is loaded from `BASE_DIR/prompts/`. Raises `ValueError` if the file does not exist.

### Step 3: Conditionally Append Constraints

If `use_constraints=True`, `prompts/user_constraints.txt` is appended to the system prompt. The file already contains its own `<constraints>...</constraints>` XML tags — no wrapping is added. If the file is missing, a warning is logged (no exception).

### Step 4: Conditionally Append Project Bank

If `use_projects=True`, `prompts/additional_projects.txt` is appended to the system prompt. It already contains `<project_bank>...</project_bank>` XML tags. If the file is missing, a warning is logged.

### Step 5: Assemble User Prompt

```python
user_prompt = f"""<resume_body>
                {resume_body}
                </resume_body>

                <job_description>
                {job_description}
                </job_description>

                <task>
                Using the rules in the system prompt, rewrite the resume body above to
                match this job description. Return only the raw LaTeX from
                \begin{{document}} to \end{{document}}.
            </task>"""
```

**Ordering is deliberate.** LLMs give higher attention to content near the end of a prompt (recency bias):
- Resume body first — bulk data, lower attention zone, ensures JD is not crowded out
- Job description second — high recency attention, the primary alignment target
- Task instruction last — maximum recency attention, ensures the model knows what to do

---

## `postprocess_latex()` — Three-Stage Cleanup

Applied to raw LLM output before `validate_latex()` and before saving to disk.

### Stage 1: Strip Markdown Fences

```python
pattern = r"```(?:latex|tex)?\n(.*?)```"
```

LLMs often wrap output in ` ```latex ... ``` ` or ` ```tex ... ``` ` fences despite being instructed not to. The regex extracts only the content inside. If no fence is found, the text is returned as-is (stripped of leading/trailing whitespace).

### Stage 2: Convert Markdown Bold to LaTeX Bold

```python
clean = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', clean)
```

Some LLMs emit `**Python**` instead of `\textbf{Python}`. This converts all instances.

### Stage 3: Remove Blank Lines Before `\resumeItem`

```python
while re.search(r'\n[ \t]*\n[ \t]*(?=\\resumeItem)', clean):
    clean = re.sub(r'\n[ \t]*\n([ \t]*\\resumeItem)', r'\n\1', clean)
```

A blank line in a LaTeX document starts a new paragraph, which causes unwanted vertical spacing between bullet points. This loop collapses all blank lines immediately before any `\resumeItem` command. The `while` loop handles multiple consecutive blank lines.

---

## `validate_latex()` — Truncation Guard

```python
def validate_latex(latex: str) -> None:
    if r"\begin{document}" not in latex or r"\end{document}" not in latex:
        raise ValueError(...)
```

Raises `ValueError` if either delimiter is missing. This catches the most common LLM failure mode: a truncated response that cuts off before `\end{document}`. Providers call this after `postprocess_latex()` and before writing the `.tex` file.

---

## Data Flow

```
master_resume_tex
        │
        ▼
  split at \begin{document}
        │
   ┌────┴────────────────────┐
   │ preamble                │ resume_body
   │ (kept, not sent to LLM) │ (sent in user_prompt)
   └─────────────────────────┘
        │
   load system_prompt.txt
   + optionally append user_constraints.txt
   + optionally append additional_projects.txt
        │
   assemble user_prompt (XML tags, ordered)
        │
        ▼
   PipelineOutput
   { system_prompt, user_prompt, preamble, raw_job_description }
        │
        ▼
   Provider.generate(request)
        │
        ▼
   raw LLM output
        │
   postprocess_latex()  →  validate_latex()  →  write .tex (preamble + body)
```

---

## Tests (`backend/tests/test_prompt_pipeline.py`)

All tests use `monkeypatch.setattr(pipeline_module, "BASE_DIR", str(tmp_path))` to redirect file loading to a temporary directory, so no real `prompts/` files are read.

A helper `_make_prompt_dir(tmp_path, system, constraints, projects)` creates the prompts directory and writes only the files passed as arguments — omitting `constraints` or `projects` simulates a missing file.

### `build_prompts` — Preamble Splitting

| Test | What it verifies |
|------|-----------------|
| `test_build_prompts_splits_preamble_correctly` | Preamble does not contain `\begin{document}`; user prompt does |
| `test_build_prompts_preserves_raw_job_description` | `raw_job_description` field equals the input JD verbatim |
| `test_build_prompts_user_prompt_contains_job_description` | JD text appears in assembled user prompt |
| `test_build_prompts_user_prompt_contains_resume_body` | Resume body content (`\section{Experience}`) appears in user prompt |
| `test_build_prompts_raises_when_no_begin_document` | `ValueError` raised if `\begin{document}` absent from input |
| `test_build_prompts_raises_when_system_prompt_missing` | `ValueError` raised if `system_prompt.txt` not found |

### `build_prompts` — Prompt File Loading

| Test | What it verifies |
|------|-----------------|
| `test_build_prompts_system_prompt_content_included` | Custom system prompt text appears in `result.system_prompt` |
| `test_build_prompts_appends_constraints_when_enabled` | Constraints content appended when `use_constraints=True` |
| `test_build_prompts_skips_constraints_when_disabled` | Constraints content absent when `use_constraints=False` |
| `test_build_prompts_warns_when_constraints_file_missing` | Log callback receives a warning mentioning `user_constraints.txt` |
| `test_build_prompts_appends_projects_when_enabled` | Project bank content appended when `use_projects=True` |
| `test_build_prompts_skips_projects_when_disabled` | Project bank content absent when `use_projects=False` |
| `test_build_prompts_warns_when_projects_file_missing` | Log callback receives a warning mentioning `additional_projects.txt` |
| `test_build_prompts_constraints_and_projects_both_appended` | Both files appended when both flags are `True` |

### `postprocess_latex` — Fence Stripping

| Test | What it verifies |
|------|-----------------|
| `test_postprocess_strips_latex_fence` | ` ```latex ` fence removed; `\begin{document}` preserved |
| `test_postprocess_strips_tex_fence` | ` ```tex ` fence removed |
| `test_postprocess_no_fence_returns_stripped_text` | Plain text (no fence) returned stripped |
| `test_postprocess_unlabeled_fence_not_stripped` | Unlabeled ` ``` ` fence: content extracted (regex matches empty label) |

### `postprocess_latex` — Bold Conversion

| Test | What it verifies |
|------|-----------------|
| `test_postprocess_converts_markdown_bold` | `**Python**` → `\textbf{Python}`; no `**` remains |
| `test_postprocess_converts_multiple_bold_instances` | Multiple `**...**` patterns all converted |
| `test_postprocess_does_not_alter_existing_textbf` | Existing `\textbf{...}` is left unchanged |

### `postprocess_latex` — Blank Line Removal

| Test | What it verifies |
|------|-----------------|
| `test_postprocess_removes_blank_line_before_resumeitem` | Single blank line before `\resumeItem` collapsed |
| `test_postprocess_removes_multiple_consecutive_blank_lines_before_resumeitem` | Multiple consecutive blank lines before `\resumeItem` all collapsed |
| `test_postprocess_preserves_blank_lines_not_before_resumeitem` | Blank lines elsewhere in the document are untouched |

### `validate_latex`

| Test | What it verifies |
|------|-----------------|
| `test_validate_latex_passes_on_valid_input` | Valid input with both delimiters does not raise |
| `test_validate_latex_raises_on_missing_begin_document` | `ValueError` raised when `\begin{document}` absent |
| `test_validate_latex_raises_on_missing_end_document` | `ValueError` raised when `\end{document}` absent |
| `test_validate_latex_raises_on_empty_string` | `ValueError` raised on empty string |
