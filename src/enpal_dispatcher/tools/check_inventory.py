"""Inventory lookup against a SQLite mirror of inventory.csv.

The mirror is built lazily on first call (and rebuilt if the CSV is newer than the DB),
so changing the CSV during development just works.
"""

from __future__ import annotations

import sqlite3
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


def check_inventory(payload: CheckInventoryInput) -> CheckInventoryOutput:
    if not payload.part_id and not payload.query:
        return CheckInventoryOutput(
            matches=[],
            summary="No part_id or query provided; cannot search inventory.",
        )

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
        rows = [_row_to_model(r) for r in cur.fetchall()]
    finally:
        con.close()

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

    summary += f"  ({len(rows)} match{'es' if len(rows) != 1 else ''} total; checked at {datetime.utcnow():%Y-%m-%d %H:%M} UTC.)"
    return CheckInventoryOutput(matches=rows, summary=summary)
