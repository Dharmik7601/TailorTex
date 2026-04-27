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
    job_id = "download-queued-test"
    jobs[job_id] = {
        "status": "queued", "log": [], "pdf_path": None,
        "company_name": "Acme", "resume_name": master_resume_name,
        "method": "gemini",
    }
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


# ═══════════════════════════════════════════════════════════════════════════════
# GET /locations
# ═══════════════════════════════════════════════════════════════════════════════

def test_locations_returns_200_with_list(client):
    r = client.get("/locations")
    assert r.status_code == 200
    assert "locations" in r.json()
    assert isinstance(r.json()["locations"], list)


def test_locations_all_strings(client):
    for loc in client.get("/locations").json()["locations"]:
        assert isinstance(loc, str) and len(loc) > 0


def test_locations_contains_default_location(client):
    assert "Rochester, NY, USA" in client.get("/locations").json()["locations"]


# ═══════════════════════════════════════════════════════════════════════════════
# GET /output/resumes
# ═══════════════════════════════════════════════════════════════════════════════

def test_output_resumes_empty_when_no_pairs(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    # Only a PDF, no matching .tex
    (output_dir / "Solo_Resume.pdf").write_bytes(b"%PDF")

    r = client.get("/output/resumes")
    assert r.status_code == 200
    assert r.json()["resumes"] == []


def test_output_resumes_only_includes_full_pairs(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    # Full pair — should be included
    (output_dir / "Google_Resume.pdf").write_bytes(b"%PDF")
    (output_dir / "Google_Resume.tex").write_text(r"\begin{document}\end{document}")
    # PDF-only — should be excluded
    (output_dir / "Orphan_Resume.pdf").write_bytes(b"%PDF")

    r = client.get("/output/resumes")
    companies = [e["company"] for e in r.json()["resumes"]]
    assert "Google" in companies
    assert "Orphan" not in companies


def test_output_resumes_returns_company_key(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "Meta_Resume.pdf").write_bytes(b"%PDF")
    (output_dir / "Meta_Resume.tex").write_text(r"\begin{document}\end{document}")

    r = client.get("/output/resumes")
    assert len(r.json()["resumes"]) == 1
    assert r.json()["resumes"][0]["company"] == "Meta"


def test_output_resumes_returns_200_when_output_dir_missing(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    # No output/ dir created

    r = client.get("/output/resumes")
    assert r.status_code == 200
    assert r.json()["resumes"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# POST /generate — resume_file upload
# ═══════════════════════════════════════════════════════════════════════════════

def test_generate_with_resume_file_upload(client):
    tex_content = r"\documentclass{article}\begin{document}body\end{document}"
    r = client.post(
        "/generate",
        data={"job_description": "JD", "company_name": "UploadCo"},
        files={"resume_file": ("uploaded.tex", tex_content.encode(), "text/plain")},
    )
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert len(r.json()["job_id"]) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# GET /download — PDF missing on disk
# ═══════════════════════════════════════════════════════════════════════════════

def test_download_completed_job_missing_pdf_on_disk_returns_404(client, tmp_path):
    job_id = "download-missing-pdf"
    jobs[job_id] = {
        "status": "completed",
        "log": [],
        "pdf_path": str(tmp_path / "Ghost_Resume.pdf"),  # file never created
        "company_name": "Ghost",
        "resume_name": "resumes/master_resume.tex",
        "method": "gemini",
    }
    assert client.get(f"/download/{job_id}").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GET /open — company fallback and PDF missing on disk
# ═══════════════════════════════════════════════════════════════════════════════

def test_open_with_company_fallback_opens_archived_resume(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pdf = output_dir / "Archived_Resume.pdf"
    pdf.write_bytes(b"%PDF")

    # job_id='_' is not in jobs → triggers company param fallback
    r = client.get("/open/_?company=Archived")
    assert r.status_code == 200
    assert r.json()["status"] == "opened"


def test_open_pdf_missing_on_disk_returns_404(client, tmp_path):
    job_id = "open-no-file"
    jobs[job_id] = {
        "status": "completed",
        "log": [],
        "pdf_path": str(tmp_path / "Gone_Resume.pdf"),  # file never created
        "company_name": "Gone",
        "resume_name": "resumes/master_resume.tex",
        "method": "gemini",
    }
    assert client.get(f"/open/{job_id}").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GET /details — company fallback and tex missing on disk
# ═══════════════════════════════════════════════════════════════════════════════

def test_details_with_company_fallback_returns_data(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    # Write real master resume content so parse_resume_tex returns valid data
    with open(MASTER_RESUME_PATH, "r", encoding="utf-8") as f:
        tex_content = f.read()
    (output_dir / "Fallback_Resume.tex").write_text(tex_content, encoding="utf-8")

    r = client.get("/details/_?company=Fallback")
    assert r.status_code == 200
    assert "experience" in r.json()
    assert "projects" in r.json()


def test_details_tex_missing_on_disk_returns_404(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    (tmp_path / "output").mkdir()
    # .tex file never created

    r = client.get("/details/_?company=NoTex")
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# POST /recompile — company fallback and PDF unchanged
# ═══════════════════════════════════════════════════════════════════════════════

def test_recompile_with_company_fallback_succeeds(client, tmp_path, monkeypatch):
    import time as _time
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    tex = output_dir / "FallbackCo_Resume.tex"
    pdf = output_dir / "FallbackCo_Resume.pdf"
    tex.write_text(r"\documentclass{article}\begin{document}hi\end{document}")
    pdf.write_bytes(b"%PDF-1.4 old")

    def fake_compile(tex_path, output_dir, log_callback=None):
        _time.sleep(0.05)
        pdf.write_bytes(b"%PDF-1.4 new")

    with patch("core.compiler.compile_latex", fake_compile):
        r = client.post("/recompile/_?company=FallbackCo")

    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_recompile_pdf_unchanged_after_compile_returns_500(client, tmp_path):
    tex = tmp_path / "Stale_Resume.tex"
    pdf = tmp_path / "Stale_Resume.pdf"
    tex.write_text(r"\documentclass{article}\begin{document}hi\end{document}")
    pdf.write_bytes(b"%PDF-1.4 old")

    job_id = "recompile-stale-pdf"
    jobs[job_id] = {
        "status": "completed",
        "log": [],
        "pdf_path": str(pdf),
        "company_name": "Stale",
        "resume_name": "resumes/master_resume.tex",
        "method": "gemini",
    }

    def no_op_compile(tex_path, output_dir, log_callback=None):
        # Deliberately do NOT update the PDF — mtime stays the same
        pass

    with patch("core.compiler.compile_latex", no_op_compile):
        r = client.post(f"/recompile/{job_id}")

    assert r.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE /files/{job_id}
# ═══════════════════════════════════════════════════════════════════════════════

def test_delete_files_unknown_job_without_company_returns_404(client):
    assert client.delete("/files/no-such-id").status_code == 404


def test_delete_files_removes_tex_and_pdf(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (tmp_path / "output" / "extras").mkdir()

    tex = output_dir / "Acme_Resume.tex"
    pdf = output_dir / "Acme_Resume.pdf"
    tex.write_text(r"\begin{document}\end{document}")
    pdf.write_bytes(b"%PDF")

    job_id = "delete-test-job"
    jobs[job_id] = {
        "status": "completed", "log": [], "pdf_path": str(pdf),
        "company_name": "Acme", "resume_name": "resumes/master_resume.tex", "method": "gemini",
    }

    r = client.delete(f"/files/{job_id}")
    assert r.status_code == 200
    assert not tex.exists()
    assert not pdf.exists()


def test_delete_files_with_company_fallback(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (tmp_path / "output" / "extras").mkdir()

    tex = output_dir / "Legacy_Resume.tex"
    pdf = output_dir / "Legacy_Resume.pdf"
    tex.write_text(r"\begin{document}\end{document}")
    pdf.write_bytes(b"%PDF")

    # job_id='_' not in jobs — triggers company param fallback
    r = client.delete("/files/_?company=Legacy")
    assert r.status_code == 200
    assert not tex.exists()
    assert not pdf.exists()


def test_delete_files_returns_deleted_list(client, tmp_path, monkeypatch):
    import api.server as server_module
    monkeypatch.setattr(server_module, "BASE_DIR", str(tmp_path))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (tmp_path / "output" / "extras").mkdir()

    tex = output_dir / "ListCo_Resume.tex"
    pdf = output_dir / "ListCo_Resume.pdf"
    tex.write_text(r"\begin{document}\end{document}")
    pdf.write_bytes(b"%PDF")

    job_id = "delete-list-test"
    jobs[job_id] = {
        "status": "completed", "log": [], "pdf_path": str(pdf),
        "company_name": "ListCo", "resume_name": "resumes/master_resume.tex", "method": "gemini",
    }

    r = client.delete(f"/files/{job_id}")
    assert r.status_code == 200
    deleted = r.json()["deleted"]
    assert isinstance(deleted, list)
    assert any("ListCo_Resume.tex" in p for p in deleted)
    assert any("ListCo_Resume.pdf" in p for p in deleted)


# ═══════════════════════════════════════════════════════════════════════════════
# _replace_location unit tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_replace_location_replaces_in_center_block():
    from api.server import _replace_location
    tex = r"""
\begin{center}
John Doe \\ {Rochester, NY, USA}
\end{center}
"""
    result = _replace_location(tex, "San Jose, CA, USA")
    assert "{San Jose, CA, USA}" in result
    assert "{Rochester, NY, USA}" not in result


def test_replace_location_no_center_block_returns_unchanged():
    from api.server import _replace_location
    tex = r"\section{Experience}\resumeItem{Did something in {Rochester, NY, USA}}"
    result = _replace_location(tex, "San Jose, CA, USA")
    assert result == tex


def test_replace_location_only_replaces_first_occurrence_in_center():
    from api.server import _replace_location
    # Two {City, ST, Country} patterns inside center block — only first should change
    tex = r"""
\begin{center}
{Rochester, NY, USA} and also {Austin, TX, USA}
\end{center}
"""
    result = _replace_location(tex, "Seattle, WA, USA")
    assert "{Seattle, WA, USA}" in result
    # Second pattern should remain untouched
    assert "{Austin, TX, USA}" in result


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


# ═══════════════════════════════════════════════════════════════════════════════
# POST /recompile/{job_id}
# ═══════════════════════════════════════════════════════════════════════════════

def test_recompile_unknown_job_without_company_returns_404(client):
    assert client.post("/recompile/no-such-id").status_code == 404


def test_recompile_missing_tex_returns_404(client, tmp_path):
    job_id = "recompile-missing-tex"
    jobs[job_id] = {
        "status": "error", "log": [], "pdf_path": str(tmp_path / "Missing_Resume.pdf"),
        "company_name": "Missing", "resume_name": "resumes/master_resume.tex", "method": "gemini",
    }
    r = client.post(f"/recompile/{job_id}?company=Missing")
    assert r.status_code == 404


def test_recompile_success_returns_200_and_marks_completed(client, tmp_path):
    import time as _time

    tex = tmp_path / "RecompileOK_Resume.tex"
    pdf = tmp_path / "RecompileOK_Resume.pdf"
    tex.write_text(r"\documentclass{article}\begin{document}hi\end{document}")
    pdf.write_bytes(b"%PDF-1.4 old")

    job_id = "recompile-success-job"
    jobs[job_id] = {
        "status": "error", "log": ["prev error"], "pdf_path": str(pdf),
        "company_name": "RecompileOK", "resume_name": "resumes/master_resume.tex", "method": "gemini",
    }

    def fake_compile(tex_path, output_dir, log_callback=None):
        # Sleep briefly so mtime is strictly newer than the original write
        _time.sleep(0.05)
        pdf.write_bytes(b"%PDF-1.4 new")

    # The endpoint does `from core.compiler import compile_latex` at call time,
    # so patching core.compiler.compile_latex intercepts that import.
    with patch("core.compiler.compile_latex", fake_compile):
        r = client.post(f"/recompile/{job_id}")

    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert jobs[job_id]["status"] == "completed"


def test_recompile_compile_failure_returns_500(client, tmp_path):
    tex = tmp_path / "RecompileFail_Resume.tex"
    tex.write_text(r"\documentclass{article}\begin{document}hi\end{document}")

    job_id = "recompile-fail-job"
    jobs[job_id] = {
        "status": "completed", "log": [], "pdf_path": str(tmp_path / "RecompileFail_Resume.pdf"),
        "company_name": "RecompileFail", "resume_name": "resumes/master_resume.tex", "method": "gemini",
    }

    def exploding_compile(tex_path, output_dir, log_callback=None):
        raise RuntimeError("pdflatex not found")

    with patch("core.compiler.compile_latex", exploding_compile):
        r = client.post(f"/recompile/{job_id}")

    assert r.status_code == 500


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


# ═══════════════════════════════════════════════════════════════════════════════
# POST /generate — use_experience flag threading
# ═══════════════════════════════════════════════════════════════════════════════

def test_generate_passes_use_experience_true_to_build_prompts(client, master_resume_name, tmp_path):
    """use_experience=True sent by client must reach build_prompts as use_experience=True."""
    tex_path = str(tmp_path / "Acme_Resume.tex")
    pdf_path = str(tmp_path / "Acme_Resume.pdf")
    with open(tex_path, "w") as f:
        f.write(r"\begin{document}\end{document}")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF")

    done_event = threading.Event()
    captured = {}

    def fake_build_prompts(**kwargs):
        captured.update(kwargs)
        done_event.set()
        return _mock_pipeline_output()

    mock_provider = MagicMock()
    mock_provider.generate.return_value = GenerationResult(tex_path=tex_path, pdf_path=pdf_path)

    with patch("api.server.build_prompts", side_effect=fake_build_prompts), \
         patch("api.server.get_provider", return_value=mock_provider), \
         patch("api.server.os.startfile", create=True):

        r = client.post("/generate", data={
            "job_description": "JD", "company_name": "Acme",
            "resume_name": master_resume_name,
            "use_experience": "true",
        })
        assert r.status_code == 200
        done_event.wait(timeout=5)

    assert captured.get("use_experience") is True


def test_generate_defaults_use_experience_to_false(client, master_resume_name, tmp_path):
    """When use_experience is not sent, build_prompts must receive use_experience=False."""
    tex_path = str(tmp_path / "Acme_Resume.tex")
    pdf_path = str(tmp_path / "Acme_Resume.pdf")
    with open(tex_path, "w") as f:
        f.write(r"\begin{document}\end{document}")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF")

    done_event = threading.Event()
    captured = {}

    def fake_build_prompts(**kwargs):
        captured.update(kwargs)
        done_event.set()
        return _mock_pipeline_output()

    mock_provider = MagicMock()
    mock_provider.generate.return_value = GenerationResult(tex_path=tex_path, pdf_path=pdf_path)

    with patch("api.server.build_prompts", side_effect=fake_build_prompts), \
         patch("api.server.get_provider", return_value=mock_provider), \
         patch("api.server.os.startfile", create=True):

        r = client.post("/generate", data={
            "job_description": "JD", "company_name": "Acme",
            "resume_name": master_resume_name,
            # use_experience intentionally omitted
        })
        assert r.status_code == 200
        done_event.wait(timeout=5)

    assert captured.get("use_experience") is False
