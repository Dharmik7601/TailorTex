import asyncio
import datetime
import json as _json
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
from core.generator import generate_resume
from core.tex_parser import parse_resume_tex

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
# ---------------------------------------------------------------------------

_work_queues: dict[str, queue.Queue] = {
    "gemini":    queue.Queue(),
    "claudecli": queue.Queue(),
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


@app.post("/generate", response_model=GenerateResponse)
async def generate(
    job_description: str = Form(...),
    company_name: str = Form(...),
    use_constraints: bool = Form(False),
    use_projects: bool = Form(False),
    resume_name: Optional[str] = Form(None),
    resume_file: Optional[UploadFile] = File(None),
    method: str = Form("gemini"),
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
        "ai_score": None,
        "judging_done": False,
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
    })

    return GenerateResponse(job_id=job_id)


def _run_judge(company_name: str, log) -> int | None:
    """Run /judge-resume, write concerns file if score >= 50, return score only. Never raises."""
    try:
        log(f"[judge] Running AI judge for {company_name}...")
        result = subprocess.run(
            ["claude", "-p", f"/judge-resume {company_name}"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            log(f"[judge] exited with code {result.returncode}: {stderr[:300]}")
            return None

        if not stdout:
            log(f"[judge] empty stdout. stderr: {stderr[:300]}")
            return None

        log(f"[judge] raw output: {stdout[:200]}")

        # Strip markdown fences if present (```json ... ```)
        stdout = re.sub(r'^```(?:json)?\s*', '', stdout, flags=re.MULTILINE)
        stdout = re.sub(r'```\s*$', '', stdout, flags=re.MULTILINE).strip()

        # Try direct parse first; fall back to extracting the first {...} block
        data = None
        try:
            data = _json.loads(stdout)
        except _json.JSONDecodeError:
            match = re.search(r'\{.*\}', stdout, re.DOTALL)
            if match:
                try:
                    data = _json.loads(match.group())
                except _json.JSONDecodeError:
                    pass

        if data is None:
            log(f"[judge] could not parse JSON. raw: {stdout[:300]}")
            return None

        score = int(data["score"])
        quotes = data.get("quotes", [])
        log(f"[judge] score={score}/100")

        if score >= 50 and quotes:
            _write_concerns(company_name, score, quotes)
            log(f"[judge] concerns written to output/resume_concerns.txt")

        return score
    except Exception:
        for line in traceback.format_exc().splitlines():
            log(f"[judge] {line}")
        return None


def _write_concerns(company_name: str, score: int, quotes: list) -> None:
    """Append a structured concerns entry to output/resume_concerns.txt."""
    output_dir = os.path.join(BASE_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    concerns_path = os.path.join(output_dir, "resume_concerns.txt")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "=" * 80,
        f"Company : {company_name}",
        f"Date    : {now}",
        f"Score   : {score}/100",
        "",
        "Suspicious Quotes:",
    ]
    for i, q in enumerate(quotes, 1):
        lines.append(f"  [{i}] \"{q.get('text', '')}\"")
        lines.append(f"       Reason: {q.get('reason', '')}")
    lines.append("=" * 80)
    lines.append("")

    with open(concerns_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _run_generation(job_id, master_resume_tex, job_description, company_name, use_constraints, use_projects, method="gemini"):
    """Called by the per-method worker thread — runs exactly one job at a time per method."""
    jobs[job_id]["status"] = "running"

    def log(msg: str):
        jobs[job_id]["log"].append(msg)

    log(f"[debug] method={method}")
    try:
        if method == "claudecli":
            pdf_path = _run_claude_cli(job_description, company_name, log)
        else:
            _, pdf_path = generate_resume(
                master_resume_tex=master_resume_tex,
                job_description=job_description,
                company_name=company_name,
                use_constraints=use_constraints,
                use_projects=use_projects,
                log_callback=log,
            )

            # Auto-open the PDF with the system default viewer
            if sys.platform == "win32":
                os.startfile(pdf_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", pdf_path])
            else:
                subprocess.run(["xdg-open", pdf_path])

        jobs[job_id]["pdf_path"] = pdf_path
        jobs[job_id]["judging_done"] = False
        jobs[job_id]["status"] = "completed"  # PDF ready — unblock SSE immediately

        def _judge_task():
            jobs[job_id]["ai_score"] = _run_judge(company_name, log)
            jobs[job_id]["judging_done"] = True

        threading.Thread(target=_judge_task, daemon=True, name=f"judge-{job_id[:8]}").start()

    except Exception as e:
        for line in traceback.format_exc().splitlines():
            log(line)
        jobs[job_id]["status"] = "error"


def _run_claude_cli(job_description: str, company_name: str, log) -> str:
    """Write JD to job_description.txt and run claude -p /tailor-resume."""
    jd_path = os.path.join(BASE_DIR, "job_description.txt")
    with open(jd_path, "w", encoding="utf-8") as f:
        f.write(job_description)

    log(f"Running Claude Code pipeline for {company_name}...")
    result = subprocess.run(
        ["claude", "-p", f"/tailor-resume {company_name}"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        for line in result.stdout.splitlines():
            log(line)
    if result.returncode != 0:
        err = result.stderr.strip() or "claude -p exited with non-zero status"
        raise RuntimeError(err)

    pdf_path = os.path.join(BASE_DIR, "output", f"{company_name}_Resume.pdf")
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"PDF not found at {pdf_path} after Claude run")
    return pdf_path


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
                completed_sent = True

            if completed_sent and job.get("judging_done"):
                payload = _json.dumps({"score": job.get("ai_score")})
                yield f"event: score\ndata: {payload}\n\n"
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
        ai_score=job.get("ai_score"),
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
