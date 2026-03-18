from typing import Optional

from pydantic import BaseModel


class GenerateResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status: str  # "queued" | "running" | "completed" | "error"
    log: list[str]
    pdf_ready: bool
    ai_score: Optional[int] = None


class QueueItem(BaseModel):
    job_id: str
    company_name: str
    resume_name: str
    method: str
    status: str
    pdf_ready: bool


class QueueResponse(BaseModel):
    jobs: list[QueueItem]
    active_count: int
