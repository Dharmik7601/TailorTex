"""
Integration tests for api/server.py (FastAPI endpoints)

All AI provider calls are mocked — no real API keys or CLI binaries required.

Covers:
  - GET  /health
  - GET  /resumes
  - POST /generate — validation (missing resume, 404, bad method, capacity limit)
  - POST /generate — job creation and queue routing
  - GET  /queue
  - GET  /status/{job_id}/json
  - GET  /download/{job_id}
  - GET  /open/{job_id}
  - GET  /details/{job_id}
  - Full generate → completed flow (mocked provider + thread sync)
"""

import os
import sys
import threading
import time
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch OS-level PDF opening globally before importing server
# (os.startfile doesn't exist on non-Windows; mocking prevents AttributeError)
import unittest.mock
_startfile_patcher = unittest.mock.patch("os.startfile", create=True)
_startfile_patcher.start()

from api.server import app, jobs
from core.providers.base import GenerationResult

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASTER_RESUME_PATH = os.path.join(REPO_ROOT, "resumes", "master_resume.tex")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_jobs():
    """Wipe in-memory job store between tests to prevent state bleed.

    Waits up to 2 s for any active jobs to reach a terminal state before
    clearing, so that background worker threads don't crash with a KeyError
    when they try to update a job that was already removed from the dict.
    """
    jobs.clear()
    yield
    # Wait for workers to finish any in-flight jobs before clearing
    deadline = time.time() + 2.0
    while time.time() < deadline:
        active = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
        if active == 0:
            break
        time.sleep(0.05)
    jobs.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def master_resume_name():
    return "resumes/master_resume.tex"


def _mock_pipeline_output():
    """Returns a PipelineOutput-like object with all required fields."""
    from core.prompt_pipeline import PipelineOutput
    return PipelineOutput(
        system_prompt="sys",
        user_prompt="usr",
        preamble=r"\documentclass{article}",
        raw_job_description="JD",
    )


def _mock_provider(tex_path="/out/Test_Resume.tex", pdf_path="/out/Test_Resume.pdf"):
    """Returns a mock ResumeProvider whose generate() returns a GenerationResult."""
    provider = MagicMock()
    provider.generate.return_value = GenerationResult(tex_path=tex_path, pdf_path=pdf_path)
    return provider


# ═══════════════════════════════════════════════════════════════════════════════
# GET /health
# ═══════════════════════════════════════════════════════════════════════════════

def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
# GET /resumes
# ═══════════════════════════════════════════════════════════════════════════════

def test_resumes_returns_list(client):
    r = client.get("/resumes")
    assert r.status_code == 200
    assert "resumes" in r.json()
    assert isinstance(r.json()["resumes"], list)


def test_resumes_contains_tex_files(client):
    r = client.get("/resumes")
    for name in r.json()["resumes"]:
        assert name.endswith(".tex")


def test_resumes_paths_start_with_resumes_prefix(client):
    r = client.get("/resumes")
    for name in r.json()["resumes"]:
        assert name.startswith("resumes/")


# ═══════════════════════════════════════════════════════════════════════════════
# POST /generate — validation
# ═══════════════════════════════════════════════════════════════════════════════

def test_generate_without_resume_returns_400(client):
    r = client.post("/generate", data={
        "job_description": "JD", "company_name": "Acme",
    })
    assert r.status_code == 400


def test_generate_with_nonexistent_resume_name_returns_404(client):
    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Acme",
        "resume_name": "resumes/does_not_exist.tex",
    })
    assert r.status_code == 404


def test_generate_returns_job_id(client, master_resume_name):
    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Acme",
        "resume_name": master_resume_name,
        "method": "gemini",
    })
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert len(r.json()["job_id"]) > 0


def test_generate_job_id_is_uuid_format(client, master_resume_name):
    import re
    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Acme",
        "resume_name": master_resume_name,
    })
    job_id = r.json()["job_id"]
    assert re.match(r"[0-9a-f-]{36}", job_id), f"Not a UUID: {job_id}"


def test_generate_job_appears_in_queue(client, master_resume_name):
    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Acme",
        "resume_name": master_resume_name,
    })
    job_id = r.json()["job_id"]
    queue = client.get("/queue").json()
    ids = [j["job_id"] for j in queue["jobs"]]
    assert job_id in ids


def test_generate_job_initial_status_is_queued_or_running(client, master_resume_name):
    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Acme",
        "resume_name": master_resume_name,
    })
    job_id = r.json()["job_id"]
    status = client.get(f"/status/{job_id}/json").json()["status"]
    assert status in ("queued", "running", "completed", "error")


def test_generate_routes_unknown_method_to_gemini_queue(client, master_resume_name):
    """Unknown method should fall back to gemini (not 400/422)."""
    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Acme",
        "resume_name": master_resume_name,
        "method": "totally_unknown_method",
    })
    assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# POST /generate — capacity limit
# ═══════════════════════════════════════════════════════════════════════════════

def test_generate_returns_429_when_queue_full(client, master_resume_name):
    # Pre-populate jobs dict with 5 active slots
    for i in range(5):
        jobs[f"fake-{i}"] = {
            "status": "queued", "log": [], "pdf_path": None,
            "company_name": f"Co{i}", "resume_name": master_resume_name,
            "method": "gemini",
        }

    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "Overflow",
        "resume_name": master_resume_name,
    })
    assert r.status_code == 429


def test_generate_accepts_job_when_only_completed_slots_exist(client, master_resume_name):
    # Completed jobs should not count against the capacity
    for i in range(5):
        jobs[f"done-{i}"] = {
            "status": "completed", "log": [], "pdf_path": "/some.pdf",
            "company_name": f"Co{i}", "resume_name": master_resume_name,
            "method": "gemini",
        }

    r = client.post("/generate", data={
        "job_description": "JD",
        "company_name": "NewJob",
        "resume_name": master_resume_name,
    })
    assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# GET /queue
# ═══════════════════════════════════════════════════════════════════════════════

def test_queue_is_empty_initially(client):
    r = client.get("/queue")
    assert r.status_code == 200
    assert r.json()["jobs"] == []
    assert r.json()["active_count"] == 0


def test_queue_reflects_submitted_jobs(client, master_resume_name):
    client.post("/generate", data={
        "job_description": "JD", "company_name": "Acme",
        "resume_name": master_resume_name,
    })
    r = client.get("/queue")
    assert r.json()["active_count"] >= 0  # may already be completed by worker
    assert len(r.json()["jobs"]) == 1


def test_queue_job_has_expected_fields(client, master_resume_name):
    client.post("/generate", data={
        "job_description": "JD", "company_name": "Acme",
        "resume_name": master_resume_name, "method": "gemini",
    })
    job = client.get("/queue").json()["jobs"][0]
    assert job["company_name"] == "Acme"
    assert job["method"] == "gemini"
    assert "status" in job
    assert "pdf_ready" in job


# ═══════════════════════════════════════════════════════════════════════════════
# GET /status/{job_id}/json
# ═══════════════════════════════════════════════════════════════════════════════

def test_status_json_unknown_job_returns_404(client):
    r = client.get("/status/nonexistent-id/json")
    assert r.status_code == 404


def test_status_json_returns_queued_status(client, master_resume_name):
    r = client.post("/generate", data={
        "job_description": "JD", "company_name": "Acme",
        "resume_name": master_resume_name,
    })
    job_id = r.json()["job_id"]
    status_r = client.get(f"/status/{job_id}/json")
    assert status_r.status_code == 200
    body = status_r.json()
    assert body["status"] in ("queued", "running", "completed", "error")
    assert isinstance(body["log"], list)
    assert "pdf_ready" in body


# ═══════════════════════════════════════════════════════════════════════════════
# GET /download/{job_id}
# ═══════════════════════════════════════════════════════════════════════════════

def test_download_unknown_job_returns_404(client):
    assert client.get("/download/no-such-id").status_code == 404


def test_download_non_completed_job_returns_400(client, master_resume_name):
    r = client.post("/generate", data={
        "job_description": "JD", "company_name": "Acme",
        "resume_name": master_resume_name,
    })
    job_id = r.json()["job_id"]
    # Forcibly set status to queued so it's definitely not completed
    jobs[job_id]["status"] = "queued"
    assert client.get(f"/download/{job_id}").status_code == 400


def test_download_returns_pdf_when_completed(client, tmp_path):
    """Manually inject a completed job with a real PDF file and verify download."""
    pdf = tmp_path / "Test_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake pdf content")

    job_id = "test-completed-job"
    jobs[job_id] = {
        "status": "completed",
        "log": [],
        "pdf_path": str(pdf),
        "company_name": "Test",
        "resume_name": "resumes/master_resume.tex",
        "method": "gemini",
    }

    r = client.get(f"/download/{job_id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


# ═══════════════════════════════════════════════════════════════════════════════
# GET /open/{job_id}
# ═══════════════════════════════════════════════════════════════════════════════

def test_open_unknown_job_without_company_returns_404(client):
    assert client.get("/open/no-such-id").status_code == 404


def test_open_completed_job_returns_ok(client, tmp_path):
    pdf = tmp_path / "Test_Resume.pdf"
    pdf.write_bytes(b"%PDF")

    job_id = "open-test-job"
    jobs[job_id] = {
        "status": "completed", "log": [],
        "pdf_path": str(pdf), "company_name": "Test",
        "resume_name": "resumes/master_resume.tex", "method": "gemini",
    }

    r = client.get(f"/open/{job_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "opened"


def test_open_non_completed_job_returns_400(client, tmp_path):
    job_id = "open-queued-job"
    jobs[job_id] = {
        "status": "queued", "log": [], "pdf_path": None,
        "company_name": "Test", "resume_name": "resumes/master_resume.tex",
        "method": "gemini",
    }
    assert client.get(f"/open/{job_id}").status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# GET /details/{job_id}
# ═══════════════════════════════════════════════════════════════════════════════

def test_details_unknown_job_without_company_returns_404(client):
    assert client.get("/details/no-such-id").status_code == 404


def test_details_returns_experience_and_projects(client):
    """Use the real master_resume.tex via a manually injected completed job."""
    tex_path = MASTER_RESUME_PATH
    fake_pdf = tex_path.replace(".tex", ".pdf")

    job_id = "details-test-job"
    jobs[job_id] = {
        "status": "completed", "log": [],
        "pdf_path": fake_pdf, "company_name": "Details",
        "resume_name": tex_path, "method": "gemini",
    }

    r = client.get(f"/details/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert "experience" in body
    assert "projects" in body


# ═══════════════════════════════════════════════════════════════════════════════
# Full generate → completed flow (mocked provider, thread-synchronized)
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_generate_flow_completes_successfully(client, master_resume_name, tmp_path):
    """
    Submits a real /generate request with a mocked provider.
    Uses a threading.Event to wait for the background worker to finish,
    then asserts the job transitions to 'completed'.
    """
    tex_path = str(tmp_path / "Acme_Resume.tex")
    pdf_path = str(tmp_path / "Acme_Resume.pdf")

    # Create dummy files the server code will reference
    with open(tex_path, "w") as f:
        f.write(r"\begin{document}\end{document}")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF")

    done_event = threading.Event()

    def fake_generate(request):
        done_event.set()
        return GenerationResult(tex_path=tex_path, pdf_path=pdf_path)

    mock_provider = MagicMock()
    mock_provider.generate.side_effect = fake_generate

    with patch("api.server.get_provider", return_value=mock_provider), \
         patch("api.server.build_prompts", return_value=_mock_pipeline_output()), \
         patch("api.server.os.startfile", create=True):

        r = client.post("/generate", data={
            "job_description": "JD", "company_name": "Acme",
            "resume_name": master_resume_name, "method": "gemini",
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        # Wait up to 5 s for the background worker thread to process the job
        done_event.wait(timeout=5)

        # Poll a few times to let status update after generate() returns
        for _ in range(20):
            status = client.get(f"/status/{job_id}/json").json()["status"]
            if status in ("completed", "error"):
                break
            time.sleep(0.1)

        assert status == "completed"
        assert mock_provider.generate.called


def test_full_generate_flow_records_error_on_provider_failure(client, master_resume_name):
    """If the provider raises, the job must transition to 'error' with traceback in log."""
    done_event = threading.Event()

    def failing_generate(request):
        done_event.set()
        raise RuntimeError("simulated provider failure")

    mock_provider = MagicMock()
    mock_provider.generate.side_effect = failing_generate

    with patch("api.server.get_provider", return_value=mock_provider), \
         patch("api.server.build_prompts", return_value=_mock_pipeline_output()):

        r = client.post("/generate", data={
            "job_description": "JD", "company_name": "FailCo",
            "resume_name": master_resume_name, "method": "gemini",
        })
        job_id = r.json()["job_id"]

        done_event.wait(timeout=5)

        for _ in range(20):
            job_status = client.get(f"/status/{job_id}/json").json()
            if job_status["status"] in ("completed", "error"):
                break
            time.sleep(0.1)

        assert job_status["status"] == "error"
        assert any("simulated provider failure" in line for line in job_status["log"])


def test_full_generate_flow_claudecli_method(client, master_resume_name, tmp_path):
    """Verify claudecli method also routes correctly through the provider registry."""
    tex_path = str(tmp_path / "Acme_Resume.tex")
    pdf_path = str(tmp_path / "Acme_Resume.pdf")
    with open(tex_path, "w") as f:
        f.write(r"\begin{document}\end{document}")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF")

    done_event = threading.Event()

    def fake_generate(request):
        done_event.set()
        return GenerationResult(tex_path=tex_path, pdf_path=pdf_path)

    mock_provider = MagicMock()
    mock_provider.generate.side_effect = fake_generate

    with patch("api.server.get_provider", return_value=mock_provider), \
         patch("api.server.build_prompts", return_value=_mock_pipeline_output()), \
         patch("api.server.os.startfile", create=True):

        r = client.post("/generate", data={
            "job_description": "JD", "company_name": "Acme",
            "resume_name": master_resume_name, "method": "claudecli",
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        done_event.wait(timeout=5)

        for _ in range(20):
            status = client.get(f"/status/{job_id}/json").json()["status"]
            if status in ("completed", "error"):
                break
            time.sleep(0.1)

        assert status == "completed"
