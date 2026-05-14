"""Centralized configuration loaded from environment / .env.

All paths are resolved absolute so tools work regardless of cwd.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


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
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- Paths ---
    chroma_persist_dir: Path = REPO_ROOT / "data" / "chroma"
    sqlite_db_path: Path = REPO_ROOT / "data" / "enpal.db"
    inventory_csv: Path = REPO_ROOT / "data" / "inventory.csv"
    technicians_csv: Path = REPO_ROOT / "data" / "technicians.csv"
    tickets_json: Path = REPO_ROOT / "data" / "tickets" / "tickets.json"
    manuals_dir: Path = REPO_ROOT / "data" / "manuals"
    known_issues_md: Path = REPO_ROOT / "data" / "known_issues.md"


settings = Settings()
