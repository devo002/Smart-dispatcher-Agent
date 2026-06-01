"""High-level entry point: run a single ticket through the dispatcher workflow.

This module exposes both a sync `triage_ticket(ticket)` for one-shot use and an
async generator `triage_ticket_stream(ticket)` that yields per-node events for the
dashboard / SSE endpoint. The latter is what powers the "watch the agent think"
moment in the demo.

Usage:
    python -m src.empire_dispatcher.agent --ticket-id T-10003
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import AsyncIterator, Iterator

from rich.console import Console
from rich.panel import Panel

from .config import settings
from .graph import build_workflow
from .graph.state import DispatchState

console = Console()


def _load_ticket(ticket_id: str) -> dict:
    raw = json.loads(Path(settings.tickets_json).read_text(encoding="utf-8"))
    for t in raw:
        if t["ticket_id"] == ticket_id:
            return t
    raise SystemExit(f"Ticket {ticket_id!r} not found in {settings.tickets_json}")


def _seed_state(ticket: dict) -> DispatchState:
    return DispatchState(
        ticket_id=ticket["ticket_id"],
        customer_name=ticket.get("customer_name", "?"),
        customer_region=ticket.get("customer_region", "Berlin"),
        raw_subject=ticket.get("subject", ""),
        raw_body=ticket.get("body", ""),
        iteration=0,
        messages=[],
    )


def triage_ticket(ticket: dict) -> dict:
    """Run the workflow synchronously and return the final state."""
    app = build_workflow()
    final = app.invoke(_seed_state(ticket))
    return final


def triage_ticket_stream(ticket: dict) -> Iterator[tuple[str, dict]]:
    """Yield (node_name, partial_state) tuples as the graph progresses.

    Useful for SSE streaming or rich CLI demos.
    """
    app = build_workflow()
    for event in app.stream(_seed_state(ticket), stream_mode="updates"):
        # event is {node_name: partial_state}
        for node_name, partial in event.items():
            yield node_name, partial


async def triage_ticket_astream(ticket: dict) -> AsyncIterator[tuple[str, dict]]:
    """Async version of the streaming runner for FastAPI / SSE."""
    app = build_workflow()
    async for event in app.astream(_seed_state(ticket), stream_mode="updates"):
        for node_name, partial in event.items():
            yield node_name, partial


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    parser = argparse.ArgumentParser(description="Run a ticket through the dispatcher.")
    parser.add_argument("--ticket-id", default="T-10003", help="ID from tickets.json")
    parser.add_argument("--stream", action="store_true", help="Show per-node updates as they happen")
    args = parser.parse_args()

    ticket = _load_ticket(args.ticket_id)
    console.print(Panel.fit(
        f"[bold]{ticket['ticket_id']}[/]  ·  {ticket['customer_name']}  ·  {ticket['customer_region']}\n\n"
        f"[dim]Subject:[/] {ticket['subject']}\n"
        f"[dim]Body:[/] {ticket['body']}",
        title="Incoming ticket",
        border_style="cyan",
    ))

    if args.stream:
        for node, partial in triage_ticket_stream(ticket):
            console.rule(f"[bold magenta]{node}")
            for k, v in partial.items():
                if k == "messages":
                    continue
                console.print(f"  [dim]{k}[/] = {v}")
        return

    final = triage_ticket(ticket)
    console.rule("[bold green]Final plan")
    console.print_json(data=final.get("plan") or {})


if __name__ == "__main__":
    _main()
