"""FastAPI surface for the dispatcher.

Endpoints:
    GET  /                         -> serves the dispatcher dashboard (single-file React UI)
    GET  /health
    GET  /tickets                  -> list seed tickets (full body included)
    GET  /tickets/{ticket_id}      -> single ticket detail
    DELETE /tickets/{ticket_id}    -> delete a submitted ticket + its jobs (needs X-Admin-Key)
    POST /tickets/triage           -> runs workflow synchronously, returns plan JSON
    POST /tickets/triage/stream    -> SSE stream of node-by-node updates
    GET  /inventory/{part_id}      -> direct inventory peek
    GET  /jobs                     -> all dispatched jobs (pending_approval | scheduled | rejected)
    GET  /jobs/{job_id}            -> single job detail
    POST /jobs/{job_id}/approve    -> dispatcher approves dispatch (status -> 'scheduled')
    POST /jobs/{job_id}/reject     -> dispatcher rejects (status -> 'rejected')
    GET  /assistant                -> serves the staff assistant chat UI
    POST /assistant/ask            -> free-form Q&A; Claude picks which lookup tool(s) to call via MCP
    GET  /intake                   -> serves the customer ticket-submission form
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import triage_ticket, triage_ticket_astream
from .config import REPO_ROOT, settings
from .ingest.build_index import build as build_index
from .staff_assistant import AssistantAnswer, ask_assistant
from .tools import CheckInventoryInput, check_inventory
from .tools.schedule_visit import JOBS_FILE


app = FastAPI(
    title="Empire Smart-Dispatcher",
    version="0.1.0",
    description="Agentic ticket triage + dispatch for residential energy support.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _ensure_index():
    """Build the ChromaDB index on first startup if it doesn't exist yet."""
    import chromadb
    try:
        client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
        client.get_collection("empire_kb")
    except Exception:
        build_index()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TicketPayload(BaseModel):
    ticket_id: str | None = Field(
        None, description="Omit to auto-generate — real customer submissions don't have one yet."
    )
    customer_name: str = "Unknown"
    customer_region: str = "Berlin"
    subject: str = ""
    body: str = Field(..., description="The customer message body.")

    def with_ticket_id(self) -> dict:
        data = self.model_dump()
        data["ticket_id"] = self.ticket_id or f"T-{uuid4().hex[:8].upper()}"
        return data


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

# Resolve relative to this file so it works both locally and after pip install
DASHBOARD_HTML = Path(__file__).resolve().parent.parent.parent / "frontend" / "dispatcher.html"
ASSISTANT_HTML = Path(__file__).resolve().parent.parent.parent / "frontend" / "staff_assistant.html"
INTAKE_HTML = Path(__file__).resolve().parent.parent.parent / "frontend" / "customer_intake.html"


@app.get("/", include_in_schema=False)
def serve_dashboard():
    if not DASHBOARD_HTML.exists():
        raise HTTPException(
            status_code=500,
            detail=f"dispatcher.html missing at {DASHBOARD_HTML}",
        )
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@app.get("/assistant", include_in_schema=False)
def serve_assistant():
    if not ASSISTANT_HTML.exists():
        raise HTTPException(
            status_code=500,
            detail=f"staff_assistant.html missing at {ASSISTANT_HTML}",
        )
    return FileResponse(ASSISTANT_HTML, media_type="text/html")


@app.get("/intake", include_in_schema=False)
def serve_intake():
    if not INTAKE_HTML.exists():
        raise HTTPException(
            status_code=500,
            detail=f"customer_intake.html missing at {INTAKE_HTML}",
        )
    return FileResponse(INTAKE_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "model": settings.claude_model}


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

SUBMITTED_TICKETS_FILE = JOBS_FILE.parent / "submitted_tickets.json"


def _load_seed_tickets() -> list[dict]:
    return json.loads(Path(settings.tickets_json).read_text(encoding="utf-8"))


def _load_submitted_tickets() -> list[dict]:
    if not SUBMITTED_TICKETS_FILE.exists():
        return []
    try:
        return json.loads(SUBMITTED_TICKETS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _append_submitted_ticket(ticket: dict) -> None:
    """Persist a real customer submission (e.g. from /intake) so it shows up in
    /tickets alongside the seed data — kept in a separate file so the static seed
    dataset (which eval/run_evals.py and the dashboard demo rely on staying fixed)
    never gets mutated by live traffic."""
    tickets = _load_submitted_tickets()
    tickets.append(ticket)
    SUBMITTED_TICKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUBMITTED_TICKETS_FILE.write_text(
        json.dumps(tickets, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _load_tickets() -> list[dict]:
    return _load_seed_tickets() + _load_submitted_tickets()


@app.get("/tickets")
def list_tickets():
    """Return all seed tickets plus real submissions, full body included so the UI
    can render the detail panel."""
    return _load_tickets()


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    for t in _load_tickets():
        if t["ticket_id"] == ticket_id:
            return t
    raise HTTPException(status_code=404, detail=f"ticket {ticket_id} not found")


def _require_admin(x_admin_key: str | None) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="admin endpoints are disabled (ADMIN_API_KEY not set)")
    if not secrets.compare_digest(x_admin_key or "", settings.admin_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing X-Admin-Key")


@app.delete("/tickets/{ticket_id}")
def delete_ticket(ticket_id: str, x_admin_key: str | None = Header(None)):
    """Delete a customer-submitted ticket and any jobs created for it.
    Seed tickets are read-only and can't be deleted."""
    _require_admin(x_admin_key)
    submitted = _load_submitted_tickets()
    remaining = [t for t in submitted if t["ticket_id"] != ticket_id]
    if len(remaining) == len(submitted):
        if any(t["ticket_id"] == ticket_id for t in _load_seed_tickets()):
            raise HTTPException(status_code=403, detail="seed tickets cannot be deleted")
        raise HTTPException(status_code=404, detail=f"ticket {ticket_id} not found")
    SUBMITTED_TICKETS_FILE.write_text(
        json.dumps(remaining, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    jobs = _load_jobs()
    kept = [j for j in jobs if j.get("ticket_id") != ticket_id]
    if len(kept) != len(jobs):
        _save_jobs(kept)
    return {"deleted": ticket_id, "jobs_removed": len(jobs) - len(kept)}


def _record_if_new_submission(payload: TicketPayload, resolved: dict) -> None:
    # payload.ticket_id is only None for a genuinely new submission (e.g. from
    # /intake) — the dashboard always replays an existing seed ticket_id, so this
    # never double-records the demo dataset.
    if payload.ticket_id is None:
        _append_submitted_ticket({
            "ticket_id": resolved["ticket_id"],
            "received_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "channel": "web_form",
            "customer_name": resolved["customer_name"],
            "customer_region": resolved["customer_region"],
            "subject": resolved["subject"],
            "body": resolved["body"],
        })


@app.post("/tickets/triage")
def triage(payload: TicketPayload):
    resolved = payload.with_ticket_id()
    _record_if_new_submission(payload, resolved)
    final = triage_ticket(resolved)
    # Strip the message log to keep the response light.
    final.pop("messages", None)
    return final


@app.post("/tickets/triage/stream")
async def triage_stream(payload: TicketPayload):
    resolved = payload.with_ticket_id()
    _record_if_new_submission(payload, resolved)

    async def event_source() -> AsyncIterator[bytes]:
        async for node, partial in triage_ticket_astream(resolved):
            partial.pop("messages", None)
            yield f"event: {node}\ndata: {json.dumps(partial, default=str)}\n\n".encode("utf-8")
        yield b"event: done\ndata: {}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Inventory peek
# ---------------------------------------------------------------------------

@app.get("/inventory/{part_id}")
def inventory_lookup(part_id: str):
    out = check_inventory(CheckInventoryInput(part_id=part_id))
    if not out.matches:
        raise HTTPException(status_code=404, detail=f"part_id {part_id} not found")
    return out.model_dump()


# ---------------------------------------------------------------------------
# Staff assistant (MCP-backed, free-form tool selection)
# ---------------------------------------------------------------------------

class AssistantAskPayload(BaseModel):
    message: str = Field(..., description="A support staffer's free-text question.")


@app.post("/assistant/ask", response_model=AssistantAnswer)
async def assistant_ask(payload: AssistantAskPayload):
    return await ask_assistant(payload.message)


# ---------------------------------------------------------------------------
# Jobs (dispatcher action surface)
# ---------------------------------------------------------------------------

def _load_jobs() -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


@app.get("/jobs")
def list_jobs():
    """Return all jobs, newest first."""
    jobs = _load_jobs()
    return list(reversed(jobs))


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    for j in _load_jobs():
        if j["job_id"] == job_id:
            return j
    raise HTTPException(status_code=404, detail=f"job {job_id} not found")


def _set_job_status(job_id: str, new_status: str, action_label: str) -> dict:
    jobs = _load_jobs()
    for j in jobs:
        if j["job_id"] == job_id:
            j["status"] = new_status
            j["last_action"] = action_label
            j["last_action_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            _save_jobs(jobs)
            return j
    raise HTTPException(status_code=404, detail=f"job {job_id} not found")


@app.post("/jobs/{job_id}/approve")
def approve_job(job_id: str):
    """Dispatcher approves the agent's plan: status flips to 'scheduled'."""
    return _set_job_status(job_id, "scheduled", "approved_by_dispatcher")


@app.post("/jobs/{job_id}/reject")
def reject_job(job_id: str):
    """Dispatcher rejects the agent's plan: status flips to 'rejected'."""
    return _set_job_status(job_id, "rejected", "rejected_by_dispatcher")


@app.post("/jobs/{job_id}/reset")
def reset_job(job_id: str):
    """Reset a job back to pending_approval so it can be re-reviewed."""
    return _set_job_status(job_id, "pending_approval", "reset_by_dispatcher")


class ReassignPayload(BaseModel):
    tech_id: str
    name: str | None = None
    region: str | None = None
    scheduled_date: str | None = None
    rationale: str | None = None


@app.post("/jobs/{job_id}/reassign")
def reassign_job(job_id: str, payload: ReassignPayload):
    """Swap the primary technician with a chosen backup."""
    jobs = _load_jobs()
    for j in jobs:
        if j["job_id"] == job_id:
            plan = j.get("plan") or {}
            tech = plan.get("technician") or {}
            tech.update({
                "tech_id": payload.tech_id,
                "name": payload.name,
                "region": payload.region,
                "scheduled_date": payload.scheduled_date or tech.get("scheduled_date"),
                "rationale": payload.rationale or "Manually reassigned by dispatcher.",
            })
            plan["technician"] = tech
            j["plan"] = plan
            j["last_action"] = "reassigned_by_dispatcher"
            j["last_action_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            _save_jobs(jobs)
            return j
    raise HTTPException(status_code=404, detail=f"job {job_id} not found")


@app.delete("/jobs")
def clear_all_jobs():
    """Wipe the jobs log — useful during development/testing."""
    _save_jobs([])
    return {"cleared": True}
