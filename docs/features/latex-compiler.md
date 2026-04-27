# Feature: LaTeX Compiler

## Files Involved

| File | Role |
|------|------|
| `backend/core/compiler.py` | Implementation — `find_pdflatex()`, `compile_latex()` |
| `backend/api/server.py` | Calls `compile_latex()` inside `/recompile` endpoint |
| `backend/core/providers/gemini.py` | Calls `compile_latex()` inside `_save_and_compile()` |
| `backend/core/providers/claude_cli.py` | Calls `compile_latex()` after validating `.tex` exists |
| `local/main.py` | Defines its own inline `compile_latex()` (duplicated, legacy) |
| `local/compile.py` | Standalone script wrapping `compile_latex()` |
| `backend/tests/test_server.py` | Indirectly tests recompile via `core.compiler.compile_latex` mock |

---

## Purpose

`compiler.py` wraps `pdflatex` into a callable Python function used by all backend providers and the recompile endpoint. It handles cross-platform binary discovery, runs the compilation subprocess, and cleans up auxiliary files.

---

## `find_pdflatex()` — Binary Discovery Chain

```python
def find_pdflatex():
    # 1. Check .env PDFLATEX_PATH first (explicit user override)
    env_path = os.environ.get("PDFLATEX_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    # 2. Check PATH (standard install, any OS)
    if shutil.which("pdflatex"):
        return "pdflatex"

    # 3. Check known install locations (fallback for non-PATH installs)
    for path in PDFLATEX_FALLBACK_PATHS:
        if os.path.exists(path):
            return path

    # 4. Return "pdflatex" anyway — will fail with a clear FileNotFoundError
    return "pdflatex"
```

### Fallback Path List

```python
PDFLATEX_FALLBACK_PATHS = [
    # Windows — MiKTeX (system-wide and per-user)
    r"C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe",
    r"C:\Program Files\MiKTeX 2.9\miktex\bin\x64\pdflatex.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"),
    # macOS — MacTeX / Homebrew
    "/Library/TeX/texbin/pdflatex",
    "/usr/local/bin/pdflatex",
    "/opt/homebrew/bin/pdflatex",
    # Linux
    "/usr/bin/pdflatex",
    "/usr/local/bin/pdflatex",
]
```

The discovery order ensures user overrides take priority, standard PATH installs work without configuration, and common non-PATH install locations are tried before failing.

---

## `compile_latex()` — Compilation and Cleanup

```python
def compile_latex(tex_path, output_dir, log_callback=print):
    cmd = [
        find_pdflatex(),
        "-interaction=nonstopmode",          # never pause for user input
        f"-output-directory={output_dir}",   # write PDF and aux files here
        tex_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        log_callback("Warning: pdflatex returned a non-zero exit code.")
        log_callback("\n".join(result.stdout.splitlines()[-10:]))   # last 10 lines
```

### Non-Zero Exit Handling

A non-zero `pdflatex` exit code does **not** raise an exception — it logs a warning and the last 10 lines of stdout. This is intentional: `pdflatex` with `-interaction=nonstopmode` often returns a non-zero code for minor warnings (e.g. missing fonts, overfull hboxes) while still producing a usable PDF. Raising on any non-zero exit would reject valid output.

The caller is responsible for checking whether the PDF was actually produced. The `/recompile` endpoint does this via mtime comparison (see below).

### Auxiliary File Cleanup

```python
base_name = os.path.splitext(os.path.basename(tex_path))[0]
for ext in [".aux", ".log", ".out"]:
    file_to_remove = os.path.join(output_dir, f"{base_name}{ext}")
    if os.path.exists(file_to_remove):
        try:
            os.remove(file_to_remove)
        except OSError as e:
            log_callback(f"Failed to remove {file_to_remove}: {e}")
```

Removes `.aux` (cross-reference data), `.log` (full compilation log), and `.out` (hyperref data). Cleanup failures are logged as warnings rather than exceptions — a failed cleanup does not invalidate the PDF.

### `log_callback` Parameter

All callers pass their own log function:
- Providers pass `request.log` → lines stream to `jobs[job_id]["log"]` → visible in extension
- The recompile endpoint passes `log_lines.append` → collected for error reporting
- Standalone scripts use the default `print`

---

## How the Recompile Endpoint Uses `compile_latex`

The `/recompile/{job_id}` endpoint in `server.py` uses mtime comparison to verify that a new PDF was actually produced:

```python
# Record mtime BEFORE compile
mtime_before = os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else None

compile_latex(tex_path, output_dir, log_callback=log_lines.append)

# Verify PDF was produced AND is newer than before
if os.path.exists(pdf_path):
    mtime_after = os.path.getmtime(pdf_path)
    success = mtime_before is None or mtime_after > mtime_before
else:
    success = False

if not success:
    raise HTTPException(status_code=500, detail="\n".join(log_lines))
```

**Why mtime and not just `os.path.exists()`?**
`pdflatex` may silently fail while leaving an old PDF in place (e.g. LaTeX errors that prevent any output update). Checking only for file existence would incorrectly report success. Comparing modification times catches this case: if the file's mtime did not advance past `mtime_before`, the compile produced no new output.

---

## Call Sites

| Caller | How it calls | What it passes as `log_callback` |
|--------|-------------|----------------------------------|
| `GeminiProvider._save_and_compile()` | `compile_latex(tex_path, output_dir, log_callback=request.log)` | provider log → job log |
| `ClaudeCliProvider.generate()` | `compile_latex(tex_path, output_dir, log_callback=request.log)` | provider log → job log |
| `server.recompile()` endpoint | `compile_latex(tex_path, output_dir, log_callback=log_lines.append)` | local list for error msg |
| `local/main.py` | Inline duplicate (no `find_pdflatex`, hardcoded `"pdflatex"`) | `print` |
| `local/compile.py` | Imports and calls `compile_latex` directly | `print` |

---

## Tests

### Direct Unit Tests (`backend/tests/test_compiler.py`)

`find_pdflatex()` and `compile_latex()` are directly tested in the dedicated `test_compiler.py` file.

| Test | What it verifies |
|------|-----------------|
| `test_find_pdflatex_uses_env_path_when_set_and_exists` | Returns `PDFLATEX_PATH` env value when set and file exists on disk |
| `test_find_pdflatex_skips_env_path_when_not_on_disk` | Falls through to `shutil.which` when env path file doesn't exist |
| `test_find_pdflatex_returns_default_when_nothing_found` | Returns `"pdflatex"` string when all checks fail |
| `test_compile_latex_nonzero_exit_logs_warning_not_raises` | Non-zero pdflatex exit code → warning logged, no exception raised |
| `test_compile_latex_file_not_found_raises_runtime_error` | `FileNotFoundError` from subprocess → re-raised as `RuntimeError` |
| `test_compile_latex_cleans_up_aux_files` | `.aux`, `.log`, `.out` removed after successful compile |
| `test_compile_latex_cleanup_oserror_logs_not_raises` | `OSError` during aux cleanup → logged via `log_callback`, no raise |

### Indirect Tests via `/recompile` (`backend/tests/test_server.py`)

`compile_latex` is mocked at `core.compiler.compile_latex` in the recompile tests:

| Test | What it verifies |
|------|-----------------|
| `test_recompile_success_returns_200_and_marks_completed` | Mock writes new PDF; mtime advances → 200, job marked `completed` |
| `test_recompile_compile_failure_returns_500` | Mock raises `RuntimeError` → endpoint returns HTTP 500 |
| `test_recompile_pdf_unchanged_after_compile_returns_500` | No-op compile (mtime unchanged) → 500 even though no exception |
| `test_recompile_with_company_fallback_succeeds` | `job_id='_'` + `?company=X` fallback path → 200 on success |

The `conftest.py` auto-mock does NOT patch `compile_latex` — recompile tests manage their own mock directly on `core.compiler.compile_latex`.
