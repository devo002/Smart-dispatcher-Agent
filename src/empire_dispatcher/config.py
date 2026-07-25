"""Centralized configuration loaded from environment / .env.

All paths are resolved absolute so tools work regardless of cwd.

On Render (or any cloud host), set DATA_DIR to the mounted persistent disk path
(e.g. /data). Read-only static data (CSVs, manuals, tickets) stays in the repo
under data/; mutable runtime data (chroma, sqlite, jobs.json) goes to DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]

# Mutable data lives here. Override with DATA_DIR env var on cloud deployments
# so it lands on a persistent disk instead of the ephemeral container filesystem.
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(REPO_ROOT / "data")))


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Required ---
    anthropic_api_key: str = ""

    # --- LangSmith (optional) ---
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "empire-smart-dispatcher"
    langsmith_endpoint: str = ""  # e.g. https://eu.api.smith.langchain.com for EU-region workspaces

    # --- Models ---
    claude_model: str = "claude-sonnet-4-6"

    # --- Mutable paths (go to persistent disk in production) ---
    chroma_persist_dir: Path = _DATA_DIR / "chroma"
    sqlite_db_path: Path = _DATA_DIR / "empire.db"

    # --- Static/read-only paths (stay in repo) ---
    inventory_csv: Path = REPO_ROOT / "data" / "inventory.csv"
    technicians_csv: Path = REPO_ROOT / "data" / "technicians.csv"
    tickets_json: Path = REPO_ROOT / "data" / "tickets" / "tickets.json"
    manuals_dir: Path = REPO_ROOT / "data" / "manuals"
    known_issues_md: Path = REPO_ROOT / "data" / "known_issues.md"

    # --- Google Sheets inventory backend (optional) ---
    # When set, check_inventory reads live from this sheet instead of the CSV/SQLite
    # mirror, so edits made directly in the sheet are reflected on the next lookup.
    # Falls back to CSV/SQLite automatically if unset or unreachable.
    google_sheets_credentials_path: Path | None = None
    google_sheets_spreadsheet_id: str = ""
    google_sheets_worksheet_name: str = "inventory"

    @field_validator("google_sheets_credentials_path", mode="after")
    @classmethod
    def _resolve_google_creds_path(cls, v: Path | None) -> Path | None:
        if v is None or v.is_absolute():
            return v
        return REPO_ROOT / v


settings = Settings()

# LangChain/LangSmith's tracer reads LANGSMITH_* directly from the process
# environment (os.environ), not from this Settings object — pydantic-settings only
# parses .env into `settings`, it never exports the values back out. Mirror them here,
# once, before any LangGraph/LangChain code runs, so tracing actually activates.
if settings.langsmith_tracing and settings.langsmith_api_key:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    if settings.langsmith_endpoint:
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
