import asyncio
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from typing import Any, Optional
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from api.schemas import GenerateResponse, JobStatus, QueueItem, QueueResponse, ResumeDetails
from core.prompt_pipeline import build_prompts
from core.providers import GenerationRequest, get_provider, registered_provider_ids
from core.tex_parser import parse_resume_tex


load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOCATIONS = [
    "Rochester, NY, USA",
    "San Jose, CA, USA",
]

app = FastAPI(title="TailorTex API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Per-method work queues + dedicated worker threads
#
# Each method has:
#   - a queue.Queue that holds job payloads
#   - a single daemon thread that drains it one job at a time
#
# This means:
#   • Two Gemini jobs run sequentially  (one queue, one worker)
#   • Two Claude jobs run sequentially  (one queue, one worker)
#   • A Gemini job + a Claude job run in parallel (separate queues/workers)
#   • No threads ever block waiting — the worker simply sleeps on queue.get()
#
# Queue dict is derived from the provider registry — adding a new provider
# automatically creates its queue and worker thread with no changes here.
# ---------------------------------------------------------------------------

_work_queues: dict[str, queue.Queue] = {
    pid: queue.Queue() for pid in registered_provider_ids()
}


def _worker(method: str) -> None:
    """Dedicated worker thread — processes jobs for one method, one at a time."""
    q = _work_queues[method]
    while True:
        payload = q.get()          # blocks cheaply until work arrives
        try:
            _run_generation(**payload)
        finally:
            q.task_done()


# Start one daemon worker thread per method at import time
for _method in _work_queues:
    t = threading.Thread(target=_worker, args=(_method,), daemon=True, name=f"worker-{_method}")
    t.start()


def _replace_location(tex: str, location: str) -> str:
    """Replace the City, ST, Country pattern inside the resume's \\begin{center} header block."""
    replacement = '{' + location + '}'

    def _replace_in_center(m: re.Match) -> str:
        return re.sub(
            r'\{[^}]+,\s*[A-Z]{2},\s*[A-Za-z ]+\}',
            lambda _: replacement,
            m.group(0),
            count=1,
        )

    return re.sub(
        r'\\begin\{center\}.*?\\end\{center\}',
        _replace_in_center,
        tex,
        count=1,
        flags=re.DOTALL,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/resumes")
def list_resumes():
    """List all available base resume .tex files."""
    resumes = []

    # resumes/ folder
    resumes_dir = os.path.join(BASE_DIR, "resumes")
    if os.path.exists(resumes_dir):
        for f in sorted(os.listdir(resumes_dir)):
            if f.endswith(".tex"):
                resumes.append(f"resumes/{f}")

    return {"resumes": resumes}


@app.get("/locations")
def list_locations():
    """Return the list of supported resume locations."""
    return {"locations": LOCATIONS}


@app.get("/output/resumes")
def list_output_resumes():
    """Return all resumes in output/ that have both a .tex and .pdf file (valid resumes)."""
    output_dir = os.path.join(BASE_DIR, "output")
    valid = []
    if os.path.exists(output_dir):
        for f in sorted(os.listdir(output_dir)):
            if f.endswith("_Resume.pdf"):
                company = f[: -len("_Resume.pdf")]
                tex_path = os.path.join(output_dir, f"{company}_Resume.tex")
                if os.path.exists(tex_path):
                    valid.append({"company": company})
    return {"resumes": valid}


@app.post("/generate", response_model=GenerateResponse)
async def generate(
    job_description: str = Form(...),
    company_name: str = Form(...),
    use_constraints: bool = Form(False),
    use_projects: bool = Form(False),
    resume_name: Optional[str] = Form(None),
    resume_file: Optional[UploadFile] = File(None),
    method: str = Form("gemini"),
    location: str = Form("Rochester, NY, USA"),
):
    # Resolve resume content
    if resume_name:
        # Load from disk
        if resume_name.startswith("resumes/"):
            path = os.path.join(BASE_DIR, resume_name)
        else:
            path = os.path.join(BASE_DIR, resume_name)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail=f"Resume '{resume_name}' not found.")
        with open(path, "r", encoding="utf-8") as f:
            master_resume_tex = f.read()
    elif resume_file:
        contents = await resume_file.read()
        master_resume_tex = contents.decode("utf-8")
    else:
        raise HTTPException(status_code=400, detail="Provide either resume_name or resume_file.")

    # Capacity check: max 5 concurrent active jobs
    active = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
    if active >= 5:
        raise HTTPException(status_code=429, detail="Queue full (5/5 slots used)")

    job_id = str(uuid4())
    jobs[job_id] = {
        "status": "queued",
        "log": [],
        "pdf_path": None,
        "company_name": company_name,
        "resume_name": resume_name or (resume_file.filename if resume_file else ""),
        "method": method,
    }

    # Route to the appropriate method queue; unknown methods fall back to gemini
    target_queue = _work_queues.get(method, _work_queues["gemini"])
    target_queue.put({
        "job_id": job_id,
        "master_resume_tex": master_resume_tex,
        "job_description": job_description,
        "company_name": company_name,
        "use_constraints": use_constraints,
        "use_projects": use_projects,
        "method": method,
        "location": location,
    })

    return GenerateResponse(job_id=job_id)


def _run_generation(job_id, master_resume_tex, job_description, company_name, use_constraints, use_projects, method="gemini", location="Rochester, NY, USA"):
    """Called by the per-method worker thread — runs exactly one job at a time per method."""
    jobs[job_id]["status"] = "running"

    def log(msg: str):
        jobs[job_id]["log"].append(msg)

    log(f"[debug] method={method}")
    log(f"[debug] location={location}")
    master_resume_tex = _replace_location(master_resume_tex, location)
    try:
        provider = get_provider(method)

        pipeline = build_prompts(
            master_resume_tex=master_resume_tex,
            job_description=job_description,
            use_constraints=use_constraints,
            use_projects=use_projects,
            log=log,
        )

        request = GenerationRequest(
            system_prompt=pipeline.system_prompt,
            user_prompt=pipeline.user_prompt,
            company_name=company_name,
            preamble=pipeline.preamble,
            raw_job_description=pipeline.raw_job_description,
            log=log,
        )

        result = provider.generate(request)

        # Auto-open the PDF with the system default viewer
        if sys.platform == "win32":
            os.startfile(result.pdf_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", result.pdf_path])
        else:
            subprocess.run(["xdg-open", result.pdf_path])

        jobs[job_id]["pdf_path"] = result.pdf_path
        jobs[job_id]["status"] = "completed"  # PDF ready — unblock SSE immediately

    except Exception:
        for line in traceback.format_exc().splitlines():
            log(line)
        jobs[job_id]["status"] = "error"


@app.get("/queue", response_model=QueueResponse)
def get_queue():
    """Return all jobs currently in the store."""
    items = [
        QueueItem(
            job_id=jid,
            company_name=j["company_name"],
            resume_name=j.get("resume_name", ""),
            method=j.get("method", "gemini"),
            status=j["status"],
            pdf_ready=j["status"] == "completed" and j["pdf_path"] is not None,
        )
        for jid, j in jobs.items()
    ]
    active_count = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
    return QueueResponse(jobs=items, active_count=active_count)


@app.get("/status/{job_id}")
async def status_stream(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        sent_index = 0
        completed_sent = False
        while True:
            job = jobs[job_id]
            current_log = job["log"]

            while sent_index < len(current_log):
                line = current_log[sent_index].replace("\n", " ")
                yield f"data: {line}\n\n"
                sent_index += 1

            if not completed_sent and job["status"] == "completed":
                yield f"event: completed\ndata: completed\n\n"
                break

            if not completed_sent and job["status"] == "error":
                yield f"event: error\ndata: error\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/status/{job_id}/json", response_model=JobStatus)
def status_json(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    return JobStatus(
        status=job["status"],
        log=job["log"],
        pdf_ready=job["status"] == "completed" and job["pdf_path"] is not None,
    )


@app.get("/download/{job_id}")
def download_pdf(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] != "completed" or not job["pdf_path"]:
        raise HTTPException(status_code=400, detail="PDF not ready")
    if not os.path.exists(job["pdf_path"]):
        raise HTTPException(status_code=404, detail="PDF file not found on disk")

    return FileResponse(
        job["pdf_path"],
        media_type="application/pdf",
        filename=os.path.basename(job["pdf_path"]),
    )


@app.get("/details/{job_id}", response_model=ResumeDetails)
def get_details(job_id: str, company: Optional[str] = None):
    """Return parsed Experience and Projects data from the generated .tex file."""
    if job_id in jobs:
        job = jobs[job_id]
        if not job.get("pdf_path"):
            raise HTTPException(status_code=404, detail="No output file for this job")
        tex_path = job["pdf_path"].replace(".pdf", ".tex")
    elif company:
        tex_path = os.path.join(BASE_DIR, "output", f"{company}_Resume.tex")
    else:
        raise HTTPException(status_code=404, detail="Job not found")

    if not os.path.exists(tex_path):
        raise HTTPException(status_code=404, detail=f"TeX file not found: {tex_path}")

    try:
        with open(tex_path, "r", encoding="utf-8") as f:
            tex_content = f.read()
        return parse_resume_tex(tex_content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {e}")


@app.post("/recompile/{job_id}")
def recompile(job_id: str, company: Optional[str] = None):
    """Recompile the .tex file for a job and update job status on success."""
    from core.compiler import compile_latex

    if job_id in jobs:
        job = jobs[job_id]
        company_name = job.get("company_name") or company
        pdf_path_stored = job.get("pdf_path")
        if pdf_path_stored:
            tex_path = pdf_path_stored.replace(".pdf", ".tex")
        elif company_name:
            tex_path = os.path.join(BASE_DIR, "output", f"{company_name}_Resume.tex")
        else:
            raise HTTPException(status_code=400, detail="Cannot determine .tex path")
    elif company:
        company_name = company
        tex_path = os.path.join(BASE_DIR, "output", f"{company}_Resume.tex")
    else:
        raise HTTPException(status_code=404, detail="Job not found")

    if not os.path.exists(tex_path):
        raise HTTPException(status_code=404, detail=f"TeX file not found: {tex_path}")

    output_dir = os.path.dirname(tex_path)
    pdf_path = tex_path.replace(".tex", ".pdf")

    # Record mtime before compile to detect whether a new PDF is produced
    mtime_before = os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else None

    log_lines: list[str] = []
    try:
        compile_latex(tex_path, output_dir, log_callback=log_lines.append)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Success = PDF now exists AND is newer than before (or freshly created)
    if os.path.exists(pdf_path):
        mtime_after = os.path.getmtime(pdf_path)
        success = mtime_before is None or mtime_after > mtime_before
    else:
        success = False

    if not success:
        detail = "\n".join(log_lines) if log_lines else "Compilation failed — PDF not produced or unchanged"
        raise HTTPException(status_code=500, detail=detail)

    # Update in-memory job if present
    if job_id in jobs:
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["pdf_path"] = pdf_path
        if log_lines:
            jobs[job_id]["log"].append("[recompile]")
            jobs[job_id]["log"].extend(log_lines)

    return {"status": "completed", "pdf_path": pdf_path}


@app.delete("/files/{job_id}")
def delete_files(job_id: str, company: Optional[str] = None):
    """Delete the .tex, .pdf, and extras files for a job from disk."""
    if job_id in jobs:
        company_name = jobs[job_id].get("company_name") or company
    elif company:
        company_name = company
    else:
        raise HTTPException(status_code=404, detail="Job not found")

    if not company_name:
        raise HTTPException(status_code=400, detail="Cannot determine company name")

    output_dir = os.path.join(BASE_DIR, "output")
    extras_dir = os.path.join(BASE_DIR, "output", "extras")
    deleted = []

    for ext in [".tex", ".pdf"]:
        path = os.path.join(output_dir, f"{company_name}_Resume{ext}")
        if os.path.exists(path):
            os.remove(path)
            deleted.append(path)

    for suffix in ["_Resume.txt", "_jd.txt"]:
        path = os.path.join(extras_dir, f"{company_name}{suffix}")
        if os.path.exists(path):
            os.remove(path)
            deleted.append(path)

    return {"deleted": deleted}


@app.get("/open/{job_id}")
def open_pdf(job_id: str, company: Optional[str] = None):
    """Open the PDF with the system default viewer on the server machine.

    Looks up the path from the in-memory job store first. If the server has
    restarted and the job is no longer in memory, falls back to reconstructing
    the path from the company name (output/{company}_Resume.pdf).
    """
    if job_id in jobs:
        job = jobs[job_id]
        if job["status"] != "completed" or not job["pdf_path"]:
            raise HTTPException(status_code=400, detail="PDF not ready")
        pdf_path = job["pdf_path"]
    elif company:
        # Server restarted — reconstruct the deterministic output path
        pdf_path = os.path.join(BASE_DIR, "output", f"{company}_Resume.pdf")
    else:
        raise HTTPException(status_code=404, detail="Job not found")

    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail=f"PDF not found on disk: {pdf_path}")

    if sys.platform == "win32":
        os.startfile(pdf_path)
    elif sys.platform == "darwin":
        subprocess.run(["open", pdf_path])
    else:
        subprocess.run(["xdg-open", pdf_path])

    return {"status": "opened"}
