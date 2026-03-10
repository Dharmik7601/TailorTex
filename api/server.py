import asyncio
import os
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.schemas import GenerateResponse, JobStatus
from core.generator import generate_resume

load_dotenv()

app = FastAPI(title="TailorTex API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(
    background_tasks: BackgroundTasks,
    resume_file: UploadFile = File(...),
    job_description: str = Form(...),
    company_name: str = Form(...),
    use_constraints: bool = Form(False),
    use_projects: bool = Form(False),
):
    contents = await resume_file.read()
    master_resume_tex = contents.decode("utf-8")

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
    )

    return GenerateResponse(job_id=job_id)


def _run_generation(job_id, master_resume_tex, job_description, company_name, use_constraints, use_projects):
    jobs[job_id]["status"] = "running"

    def log(msg: str):
        jobs[job_id]["log"].append(msg)

    try:
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


@app.get("/status/{job_id}")
async def status_stream(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        sent_index = 0
        while True:
            job = jobs[job_id]
            current_log = job["log"]

            # Send any new log lines
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

    company_name = job["company_name"]
    return FileResponse(
        job["pdf_path"],
        media_type="application/pdf",
        filename=f"{company_name}_Resume.pdf",
    )
