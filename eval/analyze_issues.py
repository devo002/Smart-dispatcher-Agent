"""Aggregate recurring-problem insight across all seed tickets — the "which error
codes / manufacturers / regions keep coming up" view that LangSmith can't give us
for free, since this app doesn't tag traces with that metadata.

Runs the real triage pipeline over every ticket in tickets.json (real Claude calls —
the in-process triage cache is per-process and doesn't survive between runs, so this
costs ~N small API calls) and tallies error_code / manufacturer / customer_region /
severity from the structured triage output, instead of relying on free-text traces.

    python -m eval.analyze_issues
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.empire_dispatcher.agent import triage_ticket
from src.empire_dispatcher.config import settings


def _load_tickets() -> list[dict]:
    return json.loads(Path(settings.tickets_json).read_text(encoding="utf-8"))


def _table(title: str, counter: Counter, total: int) -> Table:
    t = Table(title=title, show_lines=False)
    t.add_column("value")
    t.add_column("count", justify="right")
    t.add_column("share", justify="right")
    t.add_column("")
    for value, count in counter.most_common():
        flag = "[yellow]recurring[/]" if count > 1 else ""
        t.add_row(str(value), str(count), f"{count / total:.0%}", flag)
    return t


def main() -> None:
    console = Console()
    tickets = _load_tickets()

    by_error_code: Counter = Counter()
    by_manufacturer: Counter = Counter()
    by_region: Counter = Counter()
    by_severity: Counter = Counter()
    errors: list[tuple[str, str]] = []

    with console.status(f"Triaging {len(tickets)} tickets...") as status:
        for i, ticket in enumerate(tickets, start=1):
            status.update(f"Triaging {i}/{len(tickets)} — {ticket['ticket_id']}")
            try:
                final = triage_ticket(ticket)
            except Exception as e:
                errors.append((ticket["ticket_id"], str(e)[:120]))
                continue

            by_region[ticket.get("customer_region", "unknown")] += 1
            by_severity[final.get("severity") or "unknown"] += 1

            code = final.get("error_code")
            if code:
                by_error_code[code.upper()] += 1

            manufacturer = final.get("manufacturer")
            if manufacturer:
                by_manufacturer[manufacturer] += 1

    n = len(tickets)
    console.print(_table(f"Issues by error code ({sum(by_error_code.values())} of {n} tickets had one)", by_error_code, max(sum(by_error_code.values()), 1)))
    console.print(_table(f"Issues by manufacturer ({sum(by_manufacturer.values())} of {n} tickets named one)", by_manufacturer, max(sum(by_manufacturer.values()), 1)))
    console.print(_table(f"Tickets by region (all {n})", by_region, n))
    console.print(_table(f"Tickets by severity (all {n})", by_severity, n))

    if errors:
        console.rule("[bold red]Errors[/]")
        for ticket_id, msg in errors:
            console.print(f"[red]{ticket_id}[/]: {msg}")


if __name__ == "__main__":
    main()
