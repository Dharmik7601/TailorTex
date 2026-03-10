from pydantic import BaseModel


class GenerateResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status: str  # "queued" | "running" | "completed" | "error"
    log: list[str]
    pdf_ready: bool
