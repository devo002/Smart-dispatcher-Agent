"""Inventory lookup.

Primary backend: live Google Sheets read (so edits made directly in the sheet are
reflected on the next lookup, no redeploy needed). Falls back to a SQLite mirror of
inventory.csv when Google Sheets isn't configured or isn't reachable (e.g. local
tests/CI without credentials), so nothing else in the pipeline needs to know which
backend actually served the data.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from typing import Iterable

import pandas as pd
from pydantic import BaseModel, Field

from ..config import settings


class CheckInventoryInput(BaseModel):
    part_id: str | None = Field(None, description="Exact part_id, e.g. 'HUA-AC-ISO-V2'.")
    query: str | None = Field(
        None,
        description="Free-text part name to fuzzy-match (case-insensitive substring).",
    )
    region: str | None = Field(
        None,
        description="Customer region; the tool will prefer the closest warehouse.",
    )


class InventoryRow(BaseModel):
    part_id: str
    part_name: str
    manufacturer: str
    compatible_models: str
    stock_level: int
    warehouse_location: str
    price_eur: float
    status: str  # active | discontinued | backorder | workshop_only

    @property
    def in_stock(self) -> bool:
        return self.stock_level > 0 and self.status == "active"


class CheckInventoryOutput(BaseModel):
    matches: list[InventoryRow]
    summary: str


# --- region <-> warehouse heuristic for routing ---
REGION_TO_WAREHOUSE = {
    "berlin": "Berlin-Tempelhof",
    "hamburg": "Hamburg-Altona",
    "cologne": "Berlin-Tempelhof",
    "düsseldorf": "Berlin-Tempelhof",
    "dusseldorf": "Berlin-Tempelhof",
    "frankfurt": "Berlin-Tempelhof",
    "stuttgart": "Munich-Riem",
    "munich": "Munich-Riem",
    "münchen": "Munich-Riem",
    "muenchen": "Munich-Riem",
}


# ---------------------------------------------------------------------------
# Google Sheets backend (primary, when configured)
# ---------------------------------------------------------------------------

_SHEET_CACHE_TTL = 30.0  # seconds — live enough to pick up manual edits quickly,
                          # cached enough to avoid hitting the Sheets API on every call.
_SHEET_STALE_TTL = 300.0  # seconds — on a failed fetch (all retries exhausted), still
                           # serve the last known-good rows for up to 5 minutes rather
                           # than falling straight to the (possibly much staler) SQLite/CSV mirror.
_SHEET_FETCH_ATTEMPTS = 3
_SHEET_RETRY_BACKOFF = (0.5, 1.5)  # seconds to wait before attempt 2 and attempt 3
_sheet_cache: dict = {"rows": None, "fetched_at": 0.0}


def _sheets_configured() -> bool:
    return bool(
        settings.google_sheets_spreadsheet_id
        and settings.google_sheets_credentials_path
        and settings.google_sheets_credentials_path.exists()
    )


def _fetch_from_sheets() -> list[InventoryRow] | None:
    """Return live inventory rows from Google Sheets, or None on any failure so the
    caller can fall back to the CSV/SQLite mirror."""
    now = time.monotonic()
    cached = _sheet_cache["rows"]
    if cached is not None and (now - _sheet_cache["fetched_at"]) < _SHEET_CACHE_TTL:
        return cached

    records = None
    for attempt in range(_SHEET_FETCH_ATTEMPTS):
        try:
            import gspread

            gc = gspread.service_account(filename=str(settings.google_sheets_credentials_path))
            sh = gc.open_by_key(settings.google_sheets_spreadsheet_id)
            ws = sh.worksheet(settings.google_sheets_worksheet_name)
            records = ws.get_all_records()
            break
        except Exception:
            if attempt < _SHEET_FETCH_ATTEMPTS - 1:
                time.sleep(_SHEET_RETRY_BACKOFF[attempt])

    if records is None:
        # All attempts failed — serve the last known-good rows if they're still
        # within the (longer) stale-on-failure window, instead of dropping straight
        # to the SQLite/CSV mirror.
        if cached is not None and (now - _sheet_cache["fetched_at"]) < _SHEET_STALE_TTL:
            return cached
        return None

    rows: list[InventoryRow] = []
    for r in records:
        try:
            rows.append(
                InventoryRow(
                    part_id=str(r["part_id"]).strip(),
                    part_name=str(r["part_name"]).strip(),
                    manufacturer=str(r["manufacturer"]).strip(),
                    compatible_models=str(r["compatible_models"]).strip(),
                    stock_level=int(r["stock_level"]),
                    warehouse_location=str(r["warehouse_location"]).strip(),
                    price_eur=float(r["price_eur"]),
                    status=str(r["status"]).strip(),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue  # skip malformed rows (blank lines, header repeats, etc.)

    _sheet_cache["rows"] = rows
    _sheet_cache["fetched_at"] = now
    return rows


# ---------------------------------------------------------------------------
# SQLite fallback (CSV mirror, used when Sheets isn't configured/reachable)
# ---------------------------------------------------------------------------

def _ensure_db() -> sqlite3.Connection:
    db = settings.sqlite_db_path
    csv = settings.inventory_csv
    db.parent.mkdir(parents=True, exist_ok=True)

    needs_rebuild = (
        not db.exists()
        or db.stat().st_mtime < csv.stat().st_mtime
    )
    if not needs_rebuild:
        with sqlite3.connect(db) as _con:
            row = _con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='inventory'"
            ).fetchone()
            if row is None:
                needs_rebuild = True
    if needs_rebuild:
        df = pd.read_csv(csv)
        with sqlite3.connect(db) as con:
            df.to_sql("inventory", con, if_exists="replace", index=False)
            con.execute("CREATE INDEX IF NOT EXISTS ix_part_id ON inventory(part_id)")
    return sqlite3.connect(db)


def _row_to_model(r: Iterable) -> InventoryRow:
    (
        part_id,
        part_name,
        manufacturer,
        compatible_models,
        stock_level,
        warehouse_location,
        price_eur,
        status,
    ) = r
    return InventoryRow(
        part_id=part_id,
        part_name=part_name,
        manufacturer=manufacturer,
        compatible_models=compatible_models,
        stock_level=int(stock_level),
        warehouse_location=warehouse_location,
        price_eur=float(price_eur),
        status=status,
    )


def _query_sqlite(payload: CheckInventoryInput) -> list[InventoryRow]:
    con = _ensure_db()
    try:
        cur = con.cursor()
        if payload.part_id:
            cur.execute(
                "SELECT part_id, part_name, manufacturer, compatible_models, "
                "stock_level, warehouse_location, price_eur, status "
                "FROM inventory WHERE part_id = ? COLLATE NOCASE",
                (payload.part_id,),
            )
        else:
            like = f"%{(payload.query or '').lower()}%"
            cur.execute(
                "SELECT part_id, part_name, manufacturer, compatible_models, "
                "stock_level, warehouse_location, price_eur, status "
                "FROM inventory WHERE LOWER(part_name) LIKE ? OR LOWER(part_id) LIKE ? "
                "ORDER BY stock_level DESC LIMIT 8",
                (like, like),
            )
        return [_row_to_model(r) for r in cur.fetchall()]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Backend-agnostic query
# ---------------------------------------------------------------------------

def _query_sheet_rows(payload: CheckInventoryInput, rows: list[InventoryRow]) -> list[InventoryRow]:
    if payload.part_id:
        pid = payload.part_id.strip().lower()
        return [r for r in rows if r.part_id.lower() == pid]
    q = (payload.query or "").lower()
    matches = [r for r in rows if q in r.part_name.lower() or q in r.part_id.lower()]
    matches.sort(key=lambda r: -r.stock_level)
    return matches[:8]


def _query_rows(payload: CheckInventoryInput) -> tuple[list[InventoryRow], str]:
    if _sheets_configured():
        sheet_rows = _fetch_from_sheets()
        if sheet_rows is not None:
            return _query_sheet_rows(payload, sheet_rows), "google_sheets"
    return _query_sqlite(payload), "sqlite"


def check_inventory(payload: CheckInventoryInput) -> CheckInventoryOutput:
    if not payload.part_id and not payload.query:
        return CheckInventoryOutput(
            matches=[],
            summary="No part_id or query provided; cannot search inventory.",
        )

    rows, source = _query_rows(payload)

    if not rows:
        return CheckInventoryOutput(
            matches=[],
            summary=f"No inventory matches for query={payload.query!r} part_id={payload.part_id!r}.",
        )

    # Prefer the warehouse closest to the customer region.
    if payload.region:
        preferred = REGION_TO_WAREHOUSE.get(payload.region.strip().lower())
        if preferred:
            rows.sort(key=lambda r: (r.warehouse_location != preferred, -r.stock_level))

    top = rows[0]
    if top.in_stock:
        summary = (
            f"{top.part_name} ({top.part_id}) — {top.stock_level} in stock at "
            f"{top.warehouse_location}, EUR {top.price_eur:.2f}."
        )
    elif top.status == "discontinued":
        summary = (
            f"{top.part_name} ({top.part_id}) is DISCONTINUED. Need a workaround or alternative part."
        )
    elif top.status == "backorder":
        summary = (
            f"{top.part_name} ({top.part_id}) is on BACKORDER (current stock 0). "
            "Suggest temporary workaround."
        )
    elif top.status == "workshop_only":
        summary = (
            f"{top.part_name} ({top.part_id}) is workshop-only — escalate to certified partner."
        )
    else:
        summary = f"{top.part_name} ({top.part_id}) is currently out of stock."

    summary += (
        f"  ({len(rows)} match{'es' if len(rows) != 1 else ''} total; "
        f"source={source}; checked at {datetime.utcnow():%Y-%m-%d %H:%M} UTC.)"
    )
    return CheckInventoryOutput(matches=rows, summary=summary)
