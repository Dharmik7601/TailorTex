# Feature: Job Queue & API

## Files Involved

| File | Role |
|------|------|
| `backend/api/server.py` | FastAPI app — all endpoints, queue setup, job store |
| `backend/api/schemas.py` | Pydantic response models |
| `backend/core/providers/__init__.py` | `registered_provider_ids()` — drives queue creation |
| `backend/tests/test_server.py` | Integration tests for all endpoints |
| `backend/tests/conftest.py` | Auto-applied fixture: mocks `build_prompts`, `get_provider`, `os.startfile` |

---

## Purpose

`server.py` is the FastAPI application that:
- Accepts resume generation jobs via REST
- Routes them to per-method queues processed by daemon worker threads
- Streams live log output to the extension via SSE
- Serves completed PDFs and parsed resume details
- Manages job file lifecycle (recompile, delete)

---

## In-Memory Job Store

```python
jobs: dict[str, dict[str, Any]] = {}
```

A module-level dict keyed by UUID. Each entry:

```python
{
    "status":       "queued" | "running" | "completed" | "error",
    "log":          [],               # list of log strings, appended during generation
    "pdf_path":     None | str,       # absolute path to the PDF once complete
    "company_name": "Google",
    "resume_name":  "resumes/master_resume.tex",
    "method":       "gemini" | "claudecli",
}
```

**In-memory only** — lost on server restart. The fallback pattern on `/open`, `/details`, `/recompile`, and `/files` reconstructs file paths from the `company` query parameter when `job_id` is not found, making those endpoints work after restart.

---

## Per-Method Worker Queue Architecture

### Queue Derivation from Registry

```python
_work_queues: dict[str, queue.Queue] = {
    pid: queue.Queue() for pid in registered_provider_ids()
}
# → {"gemini": Queue(), "claudecli": Queue()}
```

The queue dict is derived from the provider registry at import time. Adding a new provider to the registry automatically creates its queue and worker — `server.py` never changes.

### Worker Threads

```python
def _worker(method: str) -> None:
    q = _work_queues[method]
    while True:
        payload = q.get()           # blocks on empty queue (zero CPU spin)
        try:
            _run_generation(**payload)
        finally:
            q.task_done()

for _method in _work_queues:
    t = threading.Thread(target=_worker, args=(_method,), daemon=True)
    t.start()
```

One daemon thread per method, started at import time. `daemon=True` ensures threads do not block process exit.

**Concurrency model:**
- Two Gemini jobs → sequential (single worker drains the gemini queue one at a time)
- Two Claude jobs → sequential (single worker drains the claudecli queue)
- One Gemini + one Claude → parallel (separate queues, separate threads)

Serialization within each method is intentional: Gemini benefits from rate-limit spacing; Claude CLI benefits from avoiding concurrent writes to `job_description.txt` and `output/`.

---

## `_run_generation()` — Core Job Execution

Called by the worker thread for each job:

```python
def _run_generation(job_id, master_resume_tex, job_description,
                    company_name, use_constraints, use_projects,
                    method="gemini", location="Rochester, NY, USA"):
    jobs[job_id]["status"] = "running"

    # 1. Patch location in resume header
    master_resume_tex = _replace_location(master_resume_tex, location)

    # 2. Assemble prompts (shared pipeline)
    pipeline = build_prompts(master_resume_tex, job_description, ...)

    # 3. Get provider and generate
    request = GenerationRequest(system_prompt=..., user_prompt=..., ...)
    result = get_provider(method).generate(request)

    # 4. Auto-open the PDF
    os.startfile(result.pdf_path)   # Windows; subprocess("open") on Mac, xdg-open on Linux

    # 5. Mark complete
    jobs[job_id]["pdf_path"] = result.pdf_path
    jobs[job_id]["status"] = "completed"
```

On any exception: full Python traceback is written line-by-line to `jobs[job_id]["log"]`, status is set to `"error"`.

### Location Replacement (`_replace_location`)

Before prompt assembly, the `{City, ST, Country}` pattern inside the `\begin{center}...\end{center}` header block is replaced with the selected location:

```python
def _replace_in_center(m: re.Match) -> str:
    return re.sub(
        r'\{[^}]+,\s*[A-Z]{2},\s*[A-Za-z ]+\}',
        lambda _: replacement,
        m.group(0),
        count=1,    # only first match within the center block
    )

return re.sub(
    r'\\begin\{center\}.*?\\end\{center\}',
    _replace_in_center,
    tex, count=1, flags=re.DOTALL,
)
```

Two-level regex: outer captures the `center` block; inner replaces only the first `{City, State, Country}` pattern within it. `count=1` on the inner regex prevents false matches in the Education section (where a university name may also match the pattern).

---

## SSE Streaming (`/status/{job_id}`)

```python
async def event_generator():
    sent_index = 0
    while True:
        current_log = jobs[job_id]["log"]
        while sent_index < len(current_log):
            yield f"data: {current_log[sent_index]}\n\n"
            sent_index += 1

        if job["status"] == "completed":
            yield f"event: completed\ndata: completed\n\n"
            break
        if job["status"] == "error":
            yield f"event: error\ndata: error\n\n"
            break

        await asyncio.sleep(0.5)
```

The generator polls `jobs[job_id]` every 0.5 seconds. It tracks `sent_index` to emit only new log lines each poll. Named events (`event: completed`, `event: error`) are caught by the extension's `es.addEventListener()` handlers.

---

## Capacity Limit

```python
active = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
if active >= 5:
    raise HTTPException(status_code=429, detail="Queue full (5/5 slots used)")
```

Only `queued` and `running` jobs count against the 5-slot limit. Completed and error jobs do not block new submissions.

---

## Endpoint Fallback Pattern

Four endpoints accept an optional `?company=X` query parameter. Lookup order:

```
1. job_id in jobs  → use stored data
2. job_id not found AND company provided  → reconstruct path as output/{company}_Resume.{ext}
3. job_id not found AND no company  → 404
```

The extension uses `job_id='_'` for all archived-resume requests (from the output browser), which reliably triggers path 2.

---

## All Endpoints

### Read / Info

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Returns `{"status": "ok"}` |
| GET | `/resumes` | Lists `.tex` files in `resumes/` with `resumes/` prefix |
| GET | `/locations` | Returns `LOCATIONS` list from server module |
| GET | `/output/resumes` | Lists `output/` entries where both `.tex` and `.pdf` exist |
| GET | `/job_details/{company}` | Returns stored generation inputs for a company (for Retry) |
| GET | `/queue` | Returns all jobs in the in-memory store + `active_count` |
| GET | `/status/{job_id}` | SSE stream — emits log lines, then `completed` or `error` event |
| GET | `/status/{job_id}/json` | Snapshot: `{status, log, pdf_ready}` |

### Actions

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Accepts form data, creates job, enqueues to method queue |
| GET | `/download/{job_id}` | Serves the PDF as `application/pdf` download |
| GET | `/open/{job_id}` | Opens PDF with OS default viewer on the server machine |
| GET | `/details/{job_id}` | Returns `parse_resume_tex()` result for the generated `.tex` |
| POST | `/recompile/{job_id}` | Re-runs `compile_latex()`, updates job status, checks mtime |
| DELETE | `/files/{job_id}` | Deletes `.tex`, `.pdf`, `_Resume.txt`, `_jd.txt`, and `job_details/{company}.json` from disk |

### `POST /generate` Form Fields

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `company_name` | string | required | Used in output filename |
| `job_description` | string | required | Full JD text |
| `resume_name` | string | — | Path like `resumes/master_resume.tex` |
| `resume_file` | file | — | Upload alternative to `resume_name` |
| `method` | string | `"gemini"` | Unknown methods fall back to gemini |
| `location` | string | `"Rochester, NY, USA"` | Must be in `LOCATIONS` list |
| `use_constraints` | bool | `False` | Appends `user_constraints.txt` to system prompt |
| `use_projects` | bool | `False` | Appends `additional_projects.txt` to system prompt |

### `POST /recompile/{job_id}` — mtime Check

```python
mtime_before = os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else None
compile_latex(tex_path, output_dir, log_callback=log_lines.append)
mtime_after = os.path.getmtime(pdf_path)
success = mtime_before is None or mtime_after > mtime_before
```

Checks that the PDF was actually updated (not just that the file exists), preventing false success when `pdflatex` silently fails and leaves an old PDF in place.

---

## Pydantic Schemas (`schemas.py`)

| Model | Fields |
|-------|--------|
| `GenerateResponse` | `job_id: str` |
| `JobStatus` | `status: str`, `log: list[str]`, `pdf_ready: bool` |
| `QueueItem` | `job_id`, `company_name`, `resume_name`, `method`, `status`, `pdf_ready` |
| `QueueResponse` | `jobs: list[QueueItem]`, `active_count: int` |
| `ExperienceEntry` | `company`, `tech_stack`, `dates`, `role`, `location`, `bullets: list[str]` |
| `ProjectEntry` | `name`, `tech_stack`, `bullets: list[str]` |
| `ResumeDetails` | `experience: list[ExperienceEntry]`, `projects: list[ProjectEntry]` |
| `JobDetails` | `company_name`, `job_description`, `resume_name`, `master_resume_tex`, `method`, `location`, `use_constraints`, `use_projects`, `use_experience` |

---

## Tests (`backend/tests/test_server.py` + `conftest.py`)

### `conftest.py` — Auto-Applied Fixture

`mock_api_calls` is an `autouse=True` fixture applied to every test in the suite. It patches three things at session start:

```python
with patch("api.server.build_prompts", return_value=pipeline_out), \
     patch("api.server.get_provider",  return_value=mock_provider), \
     patch("api.server.os.startfile",  create=True):
    yield
```

- **`build_prompts`** → returns a dummy `PipelineOutput` instantly (no file I/O)
- **`get_provider`** → returns a mock provider whose `generate()` returns `GenerationResult(tex_path=os.devnull, pdf_path=os.devnull)`
- **`os.startfile`** → no-op (does not exist on non-Windows; `create=True` avoids `AttributeError`)

This ensures background worker threads complete instantly and never crash with a `KeyError` when the `clear_jobs` fixture wipes the job store between tests.

### `clear_jobs` Fixture

```python
@pytest.fixture(autouse=True)
def clear_jobs():
    jobs.clear()
    yield
    deadline = time.time() + 2.0
    while time.time() < deadline:
        active = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
        if active == 0: break
        time.sleep(0.05)
    jobs.clear()
```

Clears before and after each test. The post-test wait prevents worker threads from writing to a job that was already removed.

### Endpoint Tests

| Group | Test | What it verifies |
|-------|------|-----------------|
| `/health` | `test_health_returns_ok` | 200, `{"status": "ok"}` |
| `/resumes` | `test_resumes_returns_list` | 200, `"resumes"` key present |
| `/resumes` | `test_resumes_contains_tex_files` | All names end with `.tex` |
| `/resumes` | `test_resumes_paths_start_with_resumes_prefix` | All names start with `resumes/` |
| `/locations` | `test_locations_returns_200_with_list` | 200, `locations` key is a list |
| `/locations` | `test_locations_all_strings` | All values are non-empty strings |
| `/locations` | `test_locations_contains_default_location` | `"Rochester, NY, USA"` present |
| `/output/resumes` | `test_output_resumes_empty_when_no_pairs` | PDF-only entries excluded, returns empty list |
| `/output/resumes` | `test_output_resumes_only_includes_full_pairs` | Only entries with both `.tex` and `.pdf` returned |
| `/output/resumes` | `test_output_resumes_returns_company_key` | Each entry has `company` key with correct name |
| `/output/resumes` | `test_output_resumes_returns_200_when_output_dir_missing` | 200 with empty list when `output/` doesn't exist |
| `/generate` | `test_generate_without_resume_returns_400` | 400 when no resume provided |
| `/generate` | `test_generate_with_nonexistent_resume_name_returns_404` | 404 for missing file |
| `/generate` | `test_generate_returns_job_id` | 200, non-empty `job_id` |
| `/generate` | `test_generate_job_id_is_uuid_format` | UUID format regex match |
| `/generate` | `test_generate_job_appears_in_queue` | `job_id` visible in `/queue` immediately |
| `/generate` | `test_generate_job_initial_status_is_queued_or_running` | Status is a valid lifecycle state |
| `/generate` | `test_generate_routes_unknown_method_to_gemini_queue` | Unknown method → 200 (no crash) |
| `/generate` | `test_generate_returns_429_when_queue_full` | 429 when 5 active slots pre-populated |
| `/generate` | `test_generate_accepts_job_when_only_completed_slots_exist` | Completed jobs don't count against limit |
| `/generate` | `test_generate_with_resume_file_upload` | File upload accepted as alternative to `resume_name` |
| `/queue` | `test_queue_is_empty_initially` | Empty list, `active_count=0` |
| `/queue` | `test_queue_reflects_submitted_jobs` | 1 job appears after POST |
| `/queue` | `test_queue_job_has_expected_fields` | `company_name`, `method`, `status`, `pdf_ready` present |
| `/status/json` | `test_status_json_unknown_job_returns_404` | 404 for unknown `job_id` |
| `/status/json` | `test_status_json_returns_queued_status` | 200, valid `status`, `log` is list, `pdf_ready` present |
| `/download` | `test_download_unknown_job_returns_404` | 404 |
| `/download` | `test_download_non_completed_job_returns_400` | 400 when status is `queued` |
| `/download` | `test_download_returns_pdf_when_completed` | 200, `content-type: application/pdf` |
| `/download` | `test_download_completed_job_missing_pdf_on_disk_returns_404` | 404 when completed job's PDF file is gone from disk |
| `/open` | `test_open_unknown_job_without_company_returns_404` | 404 |
| `/open` | `test_open_completed_job_returns_ok` | 200, `{"status": "opened"}` |
| `/open` | `test_open_non_completed_job_returns_400` | 400 when status is `queued` |
| `/open` | `test_open_with_company_fallback_opens_archived_resume` | `job_id='_'` + `?company=X` uses output path fallback |
| `/open` | `test_open_pdf_missing_on_disk_returns_404` | 404 when PDF file deleted from disk |
| `/details` | `test_details_unknown_job_without_company_returns_404` | 404 |
| `/details` | `test_details_returns_experience_and_projects` | Parses real `master_resume.tex` via injected job |
| `/details` | `test_details_with_company_fallback_returns_data` | `job_id='_'` + `?company=X` fallback path → parsed data |
| `/details` | `test_details_tex_missing_on_disk_returns_404` | 404 when `.tex` deleted from disk |
| `/recompile` | `test_recompile_unknown_job_without_company_returns_404` | 404 |
| `/recompile` | `test_recompile_missing_tex_returns_404` | 404 when `.tex` absent |
| `/recompile` | `test_recompile_success_returns_200_and_marks_completed` | 200, `status=completed`, in-memory job updated |
| `/recompile` | `test_recompile_compile_failure_returns_500` | 500 when `compile_latex` raises |
| `/recompile` | `test_recompile_with_company_fallback_succeeds` | `job_id='_'` + `?company=X` fallback → 200 |
| `/recompile` | `test_recompile_pdf_unchanged_after_compile_returns_500` | 500 when compile runs but PDF mtime unchanged |
| `/files` | `test_delete_files_unknown_job_without_company_returns_404` | 404 |
| `/files` | `test_delete_files_removes_tex_and_pdf` | Both `.tex` and `.pdf` deleted from disk |
| `/files` | `test_delete_files_with_company_fallback` | `job_id='_'` + `?company=X` fallback deletes files |
| `/files` | `test_delete_files_returns_deleted_list` | Response `deleted` is a list of removed paths |
| `/files` | `test_delete_files_also_removes_job_details_file` | `DELETE /files/{job_id}` also removes `output/job_details/{company}.json` |
| `/generate` | `test_generate_writes_job_details_file` | `POST /generate` creates `output/job_details/{company}.json` |
| `/generate` | `test_generate_job_details_contains_expected_fields` | Written JSON has all 9 fields matching the submitted form values |
| `/job_details` | `test_job_details_returns_200_with_settings` | `GET /job_details/{company}` returns the stored JSON as a `JobDetails` response |
| `/job_details` | `test_job_details_returns_404_for_unknown_company` | 404 when no file exists for the requested company |
| `_replace_location` | `test_replace_location_replaces_in_center_block` | Pattern inside `\begin{center}...\end{center}` replaced |
| `_replace_location` | `test_replace_location_no_center_block_returns_unchanged` | String unchanged when no center block present |
| `_replace_location` | `test_replace_location_only_replaces_first_occurrence_in_center` | Only first `{City, ST, Country}` match replaced |

### Full Flow Tests (Thread-Synchronized)

These tests bypass the `conftest.py` mock with their own `patch` context managers and use a `threading.Event` to wait for the real worker thread to finish:

```python
done_event = threading.Event()

def fake_generate(request):
    done_event.set()   # signal that worker thread reached generate()
    return GenerationResult(tex_path=..., pdf_path=...)

mock_provider.generate.side_effect = fake_generate

with patch("api.server.get_provider", return_value=mock_provider), ...:
    r = client.post("/generate", ...)
    done_event.wait(timeout=5)          # wait for worker thread
    for _ in range(20):                 # poll until status updates
        if status in ("completed", "error"): break
        time.sleep(0.1)
```

| Test | What it verifies |
|------|-----------------|
| `test_full_generate_flow_completes_successfully` | Job transitions to `"completed"` after worker runs |
| `test_full_generate_flow_records_error_on_provider_failure` | Job transitions to `"error"` with traceback in log |
| `test_full_generate_flow_claudecli_method` | `claudecli` method routes correctly and completes |
