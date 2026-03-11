import asyncio
import os
import subprocess
from typing import Any, Optional
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from api.schemas import GenerateResponse, JobStatus
from core.generator import generate_resume

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="TailorTex API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/resumes")
def list_resumes():
    """List all available base resume .tex files."""
    resumes = []

    # Root master_resume.tex
    if os.path.exists(os.path.join(BASE_DIR, "master_resume.tex")):
        resumes.append("master_resume.tex")

    # resumes/ folder
    resumes_dir = os.path.join(BASE_DIR, "resumes")
    if os.path.exists(resumes_dir):
        for f in sorted(os.listdir(resumes_dir)):
            if f.endswith(".tex") and f != "master_resume.tex":
                resumes.append(f"resumes/{f}")

    return {"resumes": resumes}


@app.post("/generate", response_model=GenerateResponse)
async def generate(
    background_tasks: BackgroundTasks,
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

    job_id = str(uuid4())
    jobs[job_id] = {
        "status": "queued",
        "log": [],
        "pdf_path": None,
        "company_name": company_name,
    }

    background_tasks.add_task(
        _run_generation,
        job_id,
        master_resume_tex,
        job_description,
        company_name,
        use_constraints,
        use_projects,
        method,
    )

    return GenerateResponse(job_id=job_id)


def _run_generation(job_id, master_resume_tex, job_description, company_name, use_constraints, use_projects, method="gemini"):
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
        jobs[job_id]["pdf_path"] = pdf_path
        jobs[job_id]["status"] = "completed"
    except Exception as e:
        log(f"Error: {e}")
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


@app.get("/status/{job_id}")
async def status_stream(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        sent_index = 0
        while True:
            job = jobs[job_id]
            current_log = job["log"]

            while sent_index < len(current_log):
                line = current_log[sent_index].replace("\n", " ")
                yield f"data: {line}\n\n"
                sent_index += 1

            if job["status"] in ("completed", "error"):
                yield f"event: {job['status']}\ndata: {job['status']}\n\n"
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
        filename=f"{job['company_name']}_Resume.pdf",
    )
