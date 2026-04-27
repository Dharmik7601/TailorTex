# Feature: Provider System

## Files Involved

| File | Role |
|------|------|
| `backend/core/providers/base.py` | `ResumeProvider` ABC, `GenerationRequest`, `GenerationResult` dataclasses |
| `backend/core/providers/registry.py` | `ModelConfig` dataclass, `GEMINI_MODEL_CHAIN` list |
| `backend/core/providers/__init__.py` | `_REGISTRY` dict, `_register()`, `get_provider()`, `registered_provider_ids()` |
| `backend/core/providers/gemini.py` | `GeminiProvider` — waterfall fallback across model chain |
| `backend/core/providers/claude_cli.py` | `ClaudeCliProvider` — subprocess invocation via `claude -p` |
| `backend/tests/test_providers.py` | Unit tests for all of the above |

---

## Purpose

The provider system is a **Strategy + Registry** pattern that cleanly separates AI providers from the orchestration layer. The server never imports a provider class directly — it only calls `get_provider(method)` from the registry. Adding a new provider requires two lines in `__init__.py`; no other file changes.

---

## Layer 1: Dataclasses (`base.py`)

### `GenerationRequest`

Everything a provider needs to produce a resume, assembled before calling `generate()`.

```python
@dataclass
class GenerationRequest:
    system_prompt: str           # fully assembled (with constraints + project bank)
    user_prompt: str             # XML-tagged: resume body + JD + task instruction
    company_name: str            # used for output filename: {company_name}_Resume.tex
    preamble: str                # LaTeX preamble — prepended to LLM output before saving
    raw_job_description: str     # raw JD text — ClaudeCliProvider writes this to disk
    log: Callable[[str], None]   # log callback → appended to jobs[job_id]["log"]
```

`preamble` and `raw_job_description` are passed through from `PipelineOutput` (see `prompt_pipeline.py`). The `log` callback lets providers write live log lines visible in the extension's Logs panel.

### `GenerationResult`

```python
@dataclass
class GenerationResult:
    tex_path: str   # absolute path to the written .tex file
    pdf_path: str   # absolute path to the compiled .pdf file
```

Returned by every `generate()` implementation on success. The server stores `pdf_path` in the job dict and uses it for the `/open` and `/download` endpoints.

---

## Layer 2: Abstract Base Class (`base.py`)

```python
class ResumeProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult: ...
```

**`provider_id`** is a stable string identifier (`"gemini"`, `"claudecli"`). It is the dict key in both `_REGISTRY` and `_work_queues`.

**`generate()`** is responsible for:
- Calling the underlying AI (API or subprocess)
- Extracting valid LaTeX from the response
- Saving the `.tex` file and compiling to PDF
- Returning `GenerationResult` on success
- Raising `RuntimeError` on failure (message is captured in job log)

Providers are **not** responsible for prompt construction (handled by `prompt_pipeline.build_prompts()`) or preamble splitting.

---

## Layer 3: Model Config (`registry.py`)

```python
@dataclass(frozen=True)
class ModelConfig:
    name: str
    supports_system_instruction: bool
    merge_system_template: str = "<system>\n{system}\n</system>\n\n{user}"
    temperature: float = 0.2
```

`frozen=True` makes `ModelConfig` immutable — the chain cannot be accidentally mutated at runtime.

`supports_system_instruction` is the key flag: models that support it receive system and user prompts as separate API parameters; models that don't (e.g. Gemma) receive a single merged string using `merge_system_template`.

### `GEMINI_MODEL_CHAIN`

```python
GEMINI_MODEL_CHAIN: list[ModelConfig] = [
    ModelConfig(name="gemma-4-31b-it",          supports_system_instruction=False),
    ModelConfig(name="gemini-3-flash-preview",   supports_system_instruction=True, temperature=0.2),
]
```

Models are tried in order from index 0 — first success wins. To add a new Google model, append a `ModelConfig` to this list. To change fallback order, reorder entries.

---

## Layer 4: Registry (`__init__.py`)

```python
_REGISTRY: dict[str, ResumeProvider] = {}

def _register(provider: ResumeProvider) -> None:
    _REGISTRY[provider.provider_id] = provider

_register(GeminiProvider())
_register(ClaudeCliProvider())

def get_provider(provider_id: str) -> ResumeProvider:
    return _REGISTRY.get(provider_id, _REGISTRY["gemini"])  # fallback to gemini

def registered_provider_ids() -> list[str]:
    return list(_REGISTRY.keys())  # used by server.py to build _work_queues
```

`get_provider()` falls back to `"gemini"` for unknown IDs — unknown method strings submitted via the extension never crash the server.

`registered_provider_ids()` is called at server import time to derive `_work_queues`. Every registered provider automatically gets its own queue and worker thread with no changes to `server.py`.

**Adding a new provider:**
1. Create `backend/core/providers/<name>.py`, implement `ResumeProvider`
2. Add two lines to `__init__.py`:
   ```python
   from core.providers.<name> import MyProvider
   _register(MyProvider())
   ```

---

## Layer 5: GeminiProvider (`gemini.py`)

### `generate()` — Top-Level Flow

```python
def generate(self, request: GenerationRequest) -> GenerationResult:
    raw = self._call_with_fallback(request)   # call AI
    clean = postprocess_latex(raw)            # strip fences, fix bold, fix blank lines
    validate_latex(clean)                     # raise if truncated
    return self._save_and_compile(request, clean)
```

### `_call_with_fallback()` — Waterfall

```python
for model_cfg in GEMINI_MODEL_CHAIN:
    try:
        if not model_cfg.supports_system_instruction:
            # merge system + user into one string, pass as contents
            contents = model_cfg.merge_system_template.format(
                system=request.system_prompt, user=request.user_prompt
            )
            response = client.models.generate_content(model=model_cfg.name, contents=contents)
        else:
            # pass system as system_instruction, user as contents
            response = client.models.generate_content(
                model=model_cfg.name,
                contents=request.user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=request.system_prompt,
                    temperature=model_cfg.temperature,
                ),
            )
        return response.text
    except Exception as e:
        request.log(f"Model {model_cfg.name} failed: {e}. Attempting fallback...")

raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")
```

Every model failure is logged. If all models fail, `RuntimeError` is raised, which the server catches and writes to the job's error log.

### `_save_and_compile()`

Prepends the preamble to the cleaned LaTeX body, writes to `output/{company_name}_Resume.tex`, calls `compile_latex()`, and returns paths.

---

## Layer 6: ClaudeCliProvider (`claude_cli.py`)

Uses Claude Code CLI (`claude -p`) as a subprocess rather than a direct API call. The `/tailor-resume` slash command reads its own prompt files and writes the `.tex` — this provider just orchestrates that.

### `generate()` — Step-by-Step

```python
def generate(self, request: GenerationRequest) -> GenerationResult:
    # Step 1: Write JD to disk (slash command reads this file)
    with open(jd_path, "w") as f:
        f.write(request.raw_job_description)

    # Step 2: Run the slash command
    result = subprocess.run(
        ["claude", "-p", f"/tailor-resume {request.company_name}"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )

    # Step 3: Validate subprocess exit
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    # Step 4: Validate .tex was produced
    if not os.path.exists(tex_path):
        raise RuntimeError(f"TeX file not found at {tex_path} after Claude run")

    # Step 5: Compile
    compile_latex(tex_path, output_dir, log_callback=request.log)

    # Step 6: Validate PDF was produced
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"PDF not found at {pdf_path} after compilation")

    return GenerationResult(tex_path=tex_path, pdf_path=pdf_path)
```

**Key difference from GeminiProvider:** `system_prompt` and `user_prompt` from `GenerationRequest` are not used — Claude reads those files internally via the slash command. Only `raw_job_description` and `company_name` matter.

**`cwd=BASE_DIR`:** The subprocess runs from the project root so that the slash command resolves `resumes/`, `prompts/`, and `output/` correctly.

---

## Tests (`backend/tests/test_providers.py`)

### `TestModelConfig`

| Test | What it verifies |
|------|-----------------|
| `test_frozen_cannot_be_mutated` | `FrozenInstanceError` raised when mutating a `ModelConfig` field |
| `test_default_temperature` | Default `temperature` is `0.2` |
| `test_default_merge_template_contains_placeholders` | `{system}` and `{user}` are present in the default template |
| `test_merge_template_formats_correctly` | `.format(system=, user=)` produces a string containing both values |

### `TestGeminiModelChain`

| Test | What it verifies |
|------|-----------------|
| `test_chain_has_at_least_two_entries` | Chain has ≥ 2 entries |
| `test_first_entry_does_not_support_system_instruction` | First model (Gemma) has `supports_system_instruction=False` |
| `test_second_entry_supports_system_instruction` | Second model (Gemini) has `supports_system_instruction=True` |
| `test_all_entries_have_non_empty_names` | No blank model names |
| `test_entries_are_model_config_instances` | All entries are `ModelConfig` instances |

### `TestGenerationRequest` / `TestGenerationResult`

| Test | What it verifies |
|------|-----------------|
| `test_has_required_fields` | All 6 fields present and non-null |
| `test_log_callback_is_callable` | `log` field accepts calls and records messages |
| `test_has_tex_and_pdf_paths` | `GenerationResult` stores both path strings |

### `TestResumeProviderInterface`

| Test | What it verifies |
|------|-----------------|
| `test_cannot_instantiate_abc` | `TypeError` raised on direct instantiation |
| `test_concrete_subclass_must_implement_both_methods` | Incomplete subclass raises `TypeError` |
| `test_concrete_subclass_works_when_both_methods_implemented` | Fully implemented subclass instantiates and returns correct `provider_id` |

### `TestProviderRegistry`

| Test | What it verifies |
|------|-----------------|
| `test_registered_ids_includes_gemini` | `"gemini"` in `registered_provider_ids()` |
| `test_registered_ids_includes_claudecli` | `"claudecli"` in `registered_provider_ids()` |
| `test_get_provider_gemini_returns_gemini_provider` | Returns `GeminiProvider` instance |
| `test_get_provider_claudecli_returns_claudecli_provider` | Returns `ClaudeCliProvider` instance |
| `test_get_provider_unknown_falls_back_to_gemini` | Unknown ID returns `GeminiProvider` |
| `test_provider_ids_are_stable_strings` | All IDs are non-empty strings |

### `TestGeminiProviderCallChain` (all mock `genai.Client`)

| Test | What it verifies |
|------|-----------------|
| `test_uses_first_model_on_success` | Only 1 API call when first model succeeds |
| `test_falls_back_to_second_model_when_first_fails` | 2 API calls; fallback log message present |
| `test_raises_runtime_error_when_all_models_fail` | `RuntimeError("All Gemini models failed")` raised |
| `test_non_system_instruction_model_merges_prompts` | First model receives merged `contents`; no `config` kwarg |
| `test_system_instruction_model_passes_config` | Second model receives `config.system_instruction == "SYSTEM"` |

### `TestGeminiProviderGenerate` (uses `tmp_path`, monkeypatches `BASE_DIR`)

| Test | What it verifies |
|------|-----------------|
| `test_generate_returns_correct_paths` | Paths end with `TestCo_Resume.tex` / `.pdf` |
| `test_generate_writes_tex_file` | `.tex` file exists on disk after `generate()` |
| `test_generate_tex_file_includes_preamble` | Written file contains `\documentclass{article}` |
| `test_generate_calls_compile_latex` | `compile_latex` mock called exactly once |
| `test_generate_raises_on_invalid_latex` | `ValueError` raised when LLM output has no delimiters |
| `test_generate_strips_markdown_fence` | Written file contains no ` ``` ` characters |

### `TestClaudeCliProviderGenerate` (mocks `subprocess.run`, `compile_latex`)

| Test | What it verifies |
|------|-----------------|
| `test_writes_jd_to_job_description_txt` | `job_description.txt` written with exact JD content |
| `test_calls_claude_subprocess_with_company_name` | `claude` and company name both appear in subprocess args |
| `test_raises_on_nonzero_subprocess_exit` | `RuntimeError` with stderr message raised on non-zero exit |
| `test_empty_stderr_on_nonzero_exit_uses_fallback_message` | Empty stderr on non-zero exit → `"claude -p exited with non-zero status"` |
| `test_raises_if_tex_file_not_produced` | `RuntimeError("TeX file not found")` if `.tex` absent |
| `test_raises_if_pdf_not_produced_after_compile` | `RuntimeError("PDF not found")` if `.pdf` absent |
| `test_returns_correct_paths_on_success` | `GenerationResult` paths end with correct filenames |
| `test_logs_subprocess_stdout` | Each stdout line from subprocess appears in the log |
