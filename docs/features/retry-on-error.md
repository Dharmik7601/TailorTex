# Feature: Retry on Error

## What It Does

When a resume generation job fails (e.g. Gemini API overload, internal service error), all
generation inputs are persisted to `output/job_details/{company_name}.json` at submission
time. An error job card in the Chrome extension shows a **Retry** button. Clicking it
fetches the stored settings from the server and re-submits an identical `/generate` request,
adding a new job to the end of the queue.

This is distinct from the **Recompile** button, which is used when the `.tex` was already
written and only PDF compilation failed. Retry is for when AI generation itself failed and no
`.tex` was produced.

## Implementation

**Task 1 — `JobDetails` schema:** Added `JobDetails` Pydantic model to `backend/api/schemas.py`
after `ResumeDetails`. Nine fields representing the full generation input set. Serves as the
response type for `GET /job_details/{company}`.

**Tasks 2–4 — Backend persistence and endpoint:**
- `POST /generate` writes `output/job_details/{company_name}.json` immediately after resolving
  resume content (before the capacity check and before enqueuing). Uses `os.makedirs(...,
  exist_ok=True)` so the directory is created on first use. Overwrites any existing file for
  the same company name (last-writer-wins).
- `GET /job_details/{company}` reads and returns the JSON file as a `JobDetails` response.
  Returns 404 if no file exists.
- `DELETE /files/{job_id}` now also removes `output/job_details/{company_name}.json` and
  includes its path in the `deleted` list when it exists.

**Task 5 — Extension Retry button:**
- `retryJob(job)` fetches `GET /job_details/{company}`, builds a FormData payload, POSTs to
  `/generate`, and calls `addJob()` to add the new job to the queue. Disables the button
  during the request to prevent double-clicks; re-enables on success or failure. Uses
  `resume_name` when it starts with `resumes/`; otherwise sends `master_resume_tex` content
  as a file blob.
- `createActionsDiv()` appends a Retry button (`.job-retry-btn`) for `error` status jobs only,
  placed between Recompile and Delete. Completed jobs do not get a Retry button.

## Key Files

- `backend/api/schemas.py` — Pydantic response models
  - `JobDetails` — 9-field model: `company_name`, `job_description`, `resume_name`, `master_resume_tex`, `method`, `location`, `use_constraints`, `use_projects`, `use_experience`
- `backend/api/server.py` — FastAPI app
  - `generate(...)` — writes `output/job_details/{company_name}.json` before enqueuing
  - `get_job_details(company)` — `GET /job_details/{company}`, reads JSON or returns 404
  - `delete_files(job_id, company)` — also deletes `output/job_details/{company}.json`
- `frontend/extension/popup.js` — Chrome extension logic
  - `retryJob(job)` — fetches job_details, re-submits /generate, adds new job to queue
  - `createActionsDiv(job, card)` — adds Retry button in the error status branch

## Testing

### Backend (pytest)

Five new tests in `backend/tests/test_server.py`:

| Test | What it verifies |
|------|-----------------|
| `test_generate_writes_job_details_file` | `POST /generate` creates `output/job_details/{company}.json` |
| `test_generate_job_details_contains_expected_fields` | JSON has all 9 expected fields with correct values |
| `test_job_details_returns_200_with_settings` | `GET /job_details/{company}` returns the stored settings |
| `test_job_details_returns_404_for_unknown_company` | 404 when no file exists for the company |
| `test_delete_files_also_removes_job_details_file` | `DELETE /files/{job_id}` removes the `.json` too |

### Extension (manual)

No automated test infrastructure for the Chrome extension. Verified manually:
- Error card shows Retry button alongside Recompile and Delete
- Completed card does NOT show Retry button
- Clicking Retry with valid job_details submits a new job and it appears in queue
- Clicking Retry when job_details is missing shows a descriptive alert
- Clicking Retry when queue is full shows 429 alert and re-enables button
- Delete removes `.json` from `output/job_details/`

### Edge Cases

- Company name with spaces or special characters in file path
- Job details file missing when Retry is clicked (shows alert, button re-enabled)
- Retry when queue is full (server returns 429 — extension shows alert, button re-enabled)
- File upload (no `resume_name`) — `master_resume_tex` content stored in JSON and sent as blob on retry
- Overwrite: submitting two jobs with the same company name — second submission overwrites the first details file (last-writer-wins, consistent with output file behaviour)
