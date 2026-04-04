from pydantic import BaseModel


class GenerateResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status: str  # "queued" | "running" | "completed" | "error"
    log: list[str]
    pdf_ready: bool


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


class ExperienceEntry(BaseModel):
    company: str
    tech_stack: str
    dates: str
    role: str
    location: str
    bullets: list[str]


class ProjectEntry(BaseModel):
    name: str
    tech_stack: str
    bullets: list[str]


class ResumeDetails(BaseModel):
    experience: list[ExperienceEntry]
    projects: list[ProjectEntry]
