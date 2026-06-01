"""Schedule a field visit. In production this would write to a CRM / Asana / Salesforce.

For the demo we append to a local jobs.json so traces are inspectable.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import settings


JOBS_FILE = settings.sqlite_db_path.parent / "jobs.json"


class ScheduleVisitInput(BaseModel):
    ticket_id: str
    tech_id: str
    scheduled_date: str = Field(..., description="YYYY-MM-DD")
    parts_to_bring: list[str] = Field(default_factory=list)
    diagnosis: str
    workaround_applied: str | None = None
    requires_approval: bool = True


class ScheduleVisitOutput(BaseModel):
    job_id: str
    status: str  # 'pending_approval' | 'scheduled'
    summary: str


def _load_jobs() -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def schedule_visit(payload: ScheduleVisitInput) -> ScheduleVisitOutput:
    jobs = _load_jobs()
    job_id = f"JOB-{1000 + len(jobs) + 1}"
    record = {
        "job_id": job_id,
        "ticket_id": payload.ticket_id,
        "tech_id": payload.tech_id,
        "scheduled_date": payload.scheduled_date,
        "parts_to_bring": payload.parts_to_bring,
        "diagnosis": payload.diagnosis,
        "workaround_applied": payload.workaround_applied,
        "status": "pending_approval" if payload.requires_approval else "scheduled",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    jobs.append(record)
    _save_jobs(jobs)
    summary = (
        f"{job_id} {'queued for dispatcher approval' if payload.requires_approval else 'SCHEDULED'} "
        f"— tech {payload.tech_id} on {payload.scheduled_date} for ticket {payload.ticket_id}."
    )
    return ScheduleVisitOutput(
        job_id=job_id,
        status=record["status"],
        summary=summary,
    )
