"""Sanity-check the data layer.

Verifies CSVs parse, ticket JSON parses, and the SQLite mirror is rebuilt.
Run this once after cloning to make sure your data is sound, before building
the Chroma index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from src.enpal_dispatcher.config import settings
from src.enpal_dispatcher.tools import CheckInventoryInput, check_inventory

console = Console()


def _check_csv(path: Path, expected_cols: list[str]) -> int:
    df = pd.read_csv(path)
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        console.print(f"[red]{path.name}: missing columns {missing}[/]")
        return 0
    return len(df)


def main() -> None:
    console.rule("[bold]Empire Smart-Dispatcher — seed data check")

    inv_n = _check_csv(
        settings.inventory_csv,
        ["part_id", "part_name", "stock_level", "warehouse_location", "price_eur", "status"],
    )
    tech_n = _check_csv(
        settings.technicians_csv,
        ["tech_id", "name", "region", "certifications", "skill_tags", "languages", "next_available"],
    )
    tickets = json.loads(settings.tickets_json.read_text(encoding="utf-8"))

    table = Table(title="Data layer", show_lines=False)
    table.add_column("File")
    table.add_column("Records", justify="right")
    table.add_row("inventory.csv", str(inv_n))
    table.add_row("technicians.csv", str(tech_n))
    table.add_row("tickets.json", str(len(tickets)))
    table.add_row("known_issues.md (exists)", "yes" if settings.known_issues_md.exists() else "NO")
    console.print(table)

    # Force the SQLite mirror to build, then probe a known part.
    out = check_inventory(CheckInventoryInput(part_id="HUA-AC-ISO-V2"))
    console.print(f"\n[bold]HUA-AC-ISO-V2 lookup:[/]\n  {out.summary}")

    out2 = check_inventory(CheckInventoryInput(query="cable", region="Berlin"))
    console.print(
        f"\n[bold]Free-text 'cable' (Berlin):[/]\n  {out2.summary}"
    )

    console.rule("[green]Seed check complete")


if __name__ == "__main__":
    main()
