# Experience Bank

## What It Does

Provides an optional experience bank file (`prompts/experience_bank.txt`) that the AI can draw from when tailoring a resume — parallel to the existing `additional_projects.txt` project bank. When opted in, the file content is appended to the system prompt inside `<experience_bank>` XML tags, giving the AI a pool of additional experience entries to swap into the resume. When not opted in, the file is ignored entirely.

## Implementation

### build_prompts() — use_experience parameter
`build_prompts()` accepts a `use_experience: bool = True` parameter. When `True`, it loads `prompts/experience_bank.txt` and appends its content to the assembled system prompt after the projects block. If the file is missing, a warning is logged via the `log` callback and the function continues without crashing. The file is expected to already contain `<experience_bank>` XML tags (same convention as `additional_projects.txt`).

### /generate endpoint and _run_generation() — use_experience threading
The `/generate` endpoint accepts `use_experience: bool = Form(False)`. This value is placed in the work queue payload dict, received by `_run_generation()` as a keyword argument, and passed through to `build_prompts()`. The default is `False` (opt-in) at the API boundary, consistent with `use_constraints` and `use_projects`.

## Key Files

- `backend/core/prompt_pipeline.py` — `build_prompts()` extended with `use_experience` parameter and conditional load block
- `backend/tests/test_prompt_pipeline.py` — 4 new tests; `_make_prompt_dir` helper extended with `experience` parameter
- `backend/api/server.py` — `use_experience` Form field, queue payload entry, `_run_generation` param and `build_prompts` call arg
- `backend/tests/test_server.py` — 2 new flow tests capturing `build_prompts` kwargs to assert `use_experience` threading

## Testing

- **Unit** — `use_experience=True` with file present → content appended to `system_prompt`
- **Unit** — `use_experience=True` with file missing → warning logged via `log`, no crash, result returned
- **Unit** — `use_experience=False` → content NOT in `system_prompt` even when file exists
- **Edge case** — `use_experience=True` + `use_projects=True` simultaneously → both files appended
- **Flow test** — POST /generate with `use_experience=True` → `build_prompts` called with `use_experience=True`
- **Default test** — POST /generate without `use_experience` → `build_prompts` called with `use_experience=False`
