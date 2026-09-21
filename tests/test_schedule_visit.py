"""schedule_visit persists jobs; JOBS_FILE is redirected to tmp_path (never data/jobs.json)."""

import json

from src.empire_dispatcher.tools.schedule_visit import ScheduleVisitInput, schedule_visit


def _payload(**kw):
    base = dict(ticket_id="T-1", tech_id="T001", scheduled_date="2026-06-01", diagnosis="AC isolator fault")
    base.update(kw)
    return ScheduleVisitInput(**base)


def test_default_requires_approval(isolated_files):
    out = schedule_visit(_payload())
    assert out.status == "pending_approval" and "queued for dispatcher approval" in out.summary


def test_requires_approval_false_schedules_directly(isolated_files):
    assert schedule_visit(_payload(requires_approval=False)).status == "scheduled"


def test_job_written_to_file_with_expected_fields(isolated_files):
    jobs_file, _ = isolated_files
    schedule_visit(_payload(parts_to_bring=["HUA-1"], workaround_applied="bypass"))
    job = json.loads(jobs_file.read_text(encoding="utf-8"))[0]
    assert job["ticket_id"] == "T-1" and job["parts_to_bring"] == ["HUA-1"]
    assert job["workaround_applied"] == "bypass" and job["created_at"].endswith("Z")


def test_job_ids_increment(isolated_files):
    assert schedule_visit(_payload()).job_id == "JOB-1001"
    assert schedule_visit(_payload()).job_id == "JOB-1002"


def test_corrupt_jobs_file_is_treated_as_empty(isolated_files):
    jobs_file, _ = isolated_files
    jobs_file.write_text("{not json", encoding="utf-8")
    assert schedule_visit(_payload()).job_id == "JOB-1001"
    assert len(json.loads(jobs_file.read_text(encoding="utf-8"))) == 1
