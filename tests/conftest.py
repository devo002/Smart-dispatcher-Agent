"""Shared fixtures. Nothing here touches the network, Claude, or the real data/ files."""

import importlib

import pytest


@pytest.fixture
def isolated_files(tmp_path, monkeypatch):
    """Redirect every mutable JSON file the API/tools write to into tmp_path."""
    from src.empire_dispatcher import api
    # tools/__init__ re-exports a function named schedule_visit, which shadows the module.
    sv = importlib.import_module("src.empire_dispatcher.tools.schedule_visit")

    jobs = tmp_path / "jobs.json"
    submitted = tmp_path / "submitted_tickets.json"
    monkeypatch.setattr(sv, "JOBS_FILE", jobs)
    monkeypatch.setattr(api, "JOBS_FILE", jobs)
    monkeypatch.setattr(api, "SUBMITTED_TICKETS_FILE", submitted)
    return jobs, submitted
