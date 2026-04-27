# Tasks

## Experience Bank

- [x] Add `use_experience` parameter to `build_prompts()` in `prompt_pipeline.py` (TDD: tests in `test_prompt_pipeline.py`)
- [x] Add `use_experience` to `/generate` endpoint and `_run_generation()` in `server.py` (TDD: tests in `test_server.py`)
- [x] Add unchecked `use-experience` checkbox to `popup.html` and wire it in `popup.js`
- [x] Add `--experience` flag to `local/main.py`
- [x] Create `prompts/experience_bank.txt` placeholder file
- [x] Update all documentation (DESIGN.md, prompt-pipeline.md, CLAUDE.md, TASKS.md)

## Retry on Error

- [x] Add `JobDetails` Pydantic model to `backend/api/schemas.py`
- [x] TDD: `POST /generate` writes `output/job_details/{company}.json` — write failing test then implement in `server.py`
- [x] TDD: `GET /job_details/{company}` endpoint — write failing test then implement in `server.py`
- [x] TDD: `DELETE /files/{job_id}` removes `output/job_details/{company}.json` — write failing test then implement in `server.py`
- [x] Add Retry button and `retryJob()` handler to `frontend/extension/popup.js`
- [x] Style `.job-retry-btn` in `frontend/extension/popup.css`
- [x] Update `docs/features/job-queue-and-api.md` with new endpoint description and test rows
- [x] Update `docs/DESIGN.md` with retry pattern and Alternatives Considered entry

## End-to-End

- [x] Run full pytest suite (`cd backend && pytest`) — all tests green before marking feature done
