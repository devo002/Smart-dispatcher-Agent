"""Centralized configuration loaded from environment / .env.

All paths are resolved absolute so tools work regardless of cwd.

On Render (or any cloud host), set DATA_DIR to the mounted persistent disk path
(e.g. /data). Read-only static data (CSVs, manuals, tickets) stays in the repo
under data/; mutable runtime data (chroma, sqlite, jobs.json) goes to DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

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


settings = Settings()
