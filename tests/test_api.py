"""FastAPI endpoints via TestClient. The workflow is stubbed (no Claude); files go to tmp_path.

TestClient is used without a `with` block on purpose, so the startup hook that builds the
ChromaDB index does not run.
"""

import json

import pytest
from fastapi.testclient import TestClient

from src.empire_dispatcher import api


@pytest.fixture
def client(isolated_files, monkeypatch):
    seen = []

    def fake_triage(ticket):
        seen.append(ticket)
        return {"ticket_id": ticket["ticket_id"], "plan": {"diagnosis": {"summary": "ok"}},
                "messages": ["strip me"]}

    async def fake_astream(ticket):
        seen.append(ticket)
        yield "triage", {"severity": "high", "messages": ["strip me"]}
        yield "decision", {"plan": {"diagnosis": {"summary": "ok"}}}

    monkeypatch.setattr(api, "triage_ticket", fake_triage)
    monkeypatch.setattr(api, "triage_ticket_astream", fake_astream)
    c = TestClient(api.app)
    c.seen = seen
    return c


def _seed_jobs(jobs_file, *jobs):
    jobs_file.write_text(json.dumps(list(jobs)), encoding="utf-8")


def _job(job_id="JOB-1001", status="pending_approval"):
    return {"job_id": job_id, "status": status, "plan": {"technician": {"tech_id": "T001"}}}


# --- system / UI -----------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/", "/assistant", "/intake"])
def test_html_pages_are_served(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


# --- tickets ---------------------------------------------------------------

def test_list_tickets_returns_seed_data(client):
    r = client.get("/tickets")
    assert r.status_code == 200 and len(r.json()) > 0 and "ticket_id" in r.json()[0]


def test_get_ticket_found_and_404(client):
    first = client.get("/tickets").json()[0]["ticket_id"]
    assert client.get(f"/tickets/{first}").json()["ticket_id"] == first
    r = client.get("/tickets/T-DOES-NOT-EXIST")
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_triage_requires_body(client):
    assert client.post("/tickets/triage", json={"subject": "no body"}).status_code == 422


def test_triage_strips_message_log(client):
    r = client.post("/tickets/triage", json={"body": "inverter down"})
    assert r.status_code == 200 and "messages" not in r.json()


def test_new_submission_gets_generated_id_and_is_persisted(client, isolated_files):
    _, submitted = isolated_files
    r = client.post("/tickets/triage", json={"body": "inverter down", "customer_name": "Jane"})
    tid = r.json()["ticket_id"]
    assert tid.startswith("T-")
    saved = json.loads(submitted.read_text(encoding="utf-8"))
    assert saved[0]["ticket_id"] == tid and saved[0]["channel"] == "web_form"
    # ...and it now shows up alongside the seed tickets.
    assert tid in [t["ticket_id"] for t in client.get("/tickets").json()]


def test_replaying_existing_ticket_id_is_not_recorded(client, isolated_files):
    _, submitted = isolated_files
    client.post("/tickets/triage", json={"ticket_id": "T-10009", "body": "replay"})
    assert not submitted.exists()


def test_stream_emits_node_events_then_done(client):
    r = client.post("/tickets/triage/stream", json={"body": "inverter down"})
    assert r.status_code == 200 and "text/event-stream" in r.headers["content-type"]
    events = [b for b in r.text.split("\n\n") if b.strip()]
    assert [e.splitlines()[0] for e in events] == ["event: triage", "event: decision", "event: done"]
    assert "messages" not in events[0]


def test_stream_records_new_submission(client, isolated_files):
    _, submitted = isolated_files
    client.post("/tickets/triage/stream", json={"body": "inverter down"})
    assert len(json.loads(submitted.read_text(encoding="utf-8"))) == 1


def test_corrupt_submitted_file_does_not_break_listing(client, isolated_files):
    _, submitted = isolated_files
    submitted.write_text("{not json", encoding="utf-8")
    assert client.get("/tickets").status_code == 200


# --- inventory -------------------------------------------------------------

def test_inventory_unknown_part_is_404(client):
    assert client.get("/inventory/NOPE-123").status_code == 404


# --- jobs ------------------------------------------------------------------

def test_jobs_empty_when_no_file(client):
    assert client.get("/jobs").json() == []


def test_jobs_listed_newest_first(client, isolated_files):
    jobs_file, _ = isolated_files
    _seed_jobs(jobs_file, _job("JOB-1001"), _job("JOB-1002"))
    assert [j["job_id"] for j in client.get("/jobs").json()] == ["JOB-1002", "JOB-1001"]


def test_get_job_found_and_404(client, isolated_files):
    jobs_file, _ = isolated_files
    _seed_jobs(jobs_file, _job())
    assert client.get("/jobs/JOB-1001").json()["job_id"] == "JOB-1001"
    assert client.get("/jobs/JOB-9999").status_code == 404


@pytest.mark.parametrize("action,status,label", [
    ("approve", "scheduled", "approved_by_dispatcher"),
    ("reject", "rejected", "rejected_by_dispatcher"),
    ("reset", "pending_approval", "reset_by_dispatcher"),
])
def test_job_status_transitions(client, isolated_files, action, status, label):
    jobs_file, _ = isolated_files
    _seed_jobs(jobs_file, _job(status="scheduled"))
    r = client.post(f"/jobs/JOB-1001/{action}")
    assert r.status_code == 200
    assert r.json()["status"] == status and r.json()["last_action"] == label
    # Persisted, not just returned.
    assert json.loads(jobs_file.read_text(encoding="utf-8"))[0]["status"] == status


@pytest.mark.parametrize("action", ["approve", "reject", "reset"])
def test_job_transition_on_unknown_job_is_404(client, action):
    assert client.post(f"/jobs/JOB-9999/{action}").status_code == 404


def test_reassign_swaps_technician_and_records_action(client, isolated_files):
    jobs_file, _ = isolated_files
    _seed_jobs(jobs_file, _job())
    r = client.post("/jobs/JOB-1001/reassign", json={"tech_id": "T004", "name": "Aylin Demir"})
    tech = r.json()["plan"]["technician"]
    assert tech["tech_id"] == "T004" and tech["name"] == "Aylin Demir"
    assert tech["rationale"] == "Manually reassigned by dispatcher."
    assert r.json()["last_action"] == "reassigned_by_dispatcher"


def test_reassign_requires_tech_id(client, isolated_files):
    jobs_file, _ = isolated_files
    _seed_jobs(jobs_file, _job())
    assert client.post("/jobs/JOB-1001/reassign", json={}).status_code == 422


def test_reassign_unknown_job_is_404(client):
    assert client.post("/jobs/JOB-9999/reassign", json={"tech_id": "T004"}).status_code == 404


def test_clear_all_jobs(client, isolated_files):
    jobs_file, _ = isolated_files
    _seed_jobs(jobs_file, _job())
    assert client.delete("/jobs").json() == {"cleared": True}
    assert client.get("/jobs").json() == []


def test_corrupt_jobs_file_is_treated_as_empty(client, isolated_files):
    jobs_file, _ = isolated_files
    jobs_file.write_text("{not json", encoding="utf-8")
    assert client.get("/jobs").json() == []


# --- payload model ---------------------------------------------------------

def test_with_ticket_id_keeps_supplied_id():
    assert api.TicketPayload(ticket_id="T-1", body="x").with_ticket_id()["ticket_id"] == "T-1"


def test_with_ticket_id_generates_unique_ids():
    a = api.TicketPayload(body="x").with_ticket_id()["ticket_id"]
    b = api.TicketPayload(body="x").with_ticket_id()["ticket_id"]
    assert a != b and a.startswith("T-")


# --- DELETE /tickets/{id} (admin) -------------------------------------------

@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(api.settings, "admin_api_key", "secret")
    return {"X-Admin-Key": "secret"}


def _submit(client, isolated_files):
    tid = client.post("/tickets/triage", json={"body": "fan not working"}).json()["ticket_id"]
    return tid


def test_delete_ticket_removes_it_and_its_jobs(client, isolated_files, admin):
    jobs_file, submitted = isolated_files
    tid = _submit(client, isolated_files)
    _seed_jobs(jobs_file, {**_job("JOB-1001"), "ticket_id": tid}, {**_job("JOB-1002"), "ticket_id": "other"})
    r = client.delete(f"/tickets/{tid}", headers=admin)
    assert r.status_code == 200 and r.json() == {"deleted": tid, "jobs_removed": 1}
    assert tid not in [t["ticket_id"] for t in client.get("/tickets").json()]
    assert [j["job_id"] for j in client.get("/jobs").json()] == ["JOB-1002"]


def test_delete_ticket_requires_key(client, isolated_files, admin):
    tid = _submit(client, isolated_files)
    assert client.delete(f"/tickets/{tid}").status_code == 401
    assert client.delete(f"/tickets/{tid}", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert tid in [t["ticket_id"] for t in client.get("/tickets").json()]


def test_delete_ticket_disabled_when_no_key_configured(client, isolated_files, monkeypatch):
    monkeypatch.setattr(api.settings, "admin_api_key", "")
    assert client.delete("/tickets/T-1", headers={"X-Admin-Key": ""}).status_code == 503


def test_delete_seed_ticket_is_forbidden(client, admin):
    seed = client.get("/tickets").json()[0]["ticket_id"]
    assert client.delete(f"/tickets/{seed}", headers=admin).status_code == 403


def test_delete_unknown_ticket_is_404(client, admin):
    assert client.delete("/tickets/T-NOPE", headers=admin).status_code == 404
