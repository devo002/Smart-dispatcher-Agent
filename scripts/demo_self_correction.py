"""The headline demo — show the agent self-correcting.

Ticket T-10003 (Frau Müller, Hamburg, Error 602 with long AC cable run) requires
part HUA-AC-ISO-V2, which is DISCONTINUED in inventory.csv. The agent should:

    1. Triage  -> identify error 602, manufacturer Huawei
    2. Research -> find HUA-602 entry in known_issues.md, propose HUA-AC-ISO-V2
    3. Inventory -> miss (status='discontinued', stock=0)
    4. Research (loop) -> refined query finds the firmware-update workaround
    5. Inventory -> no part needed
    6. Routing  -> pick a Hamburg-region Huawei-certified tech
    7. Decision -> compose plan with workaround note, queue for dispatcher approval

Run:
    python -m scripts.demo_self_correction
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from src.empire_dispatcher.agent import triage_ticket_stream
from src.empire_dispatcher.config import settings


console = Console()
DEMO_TICKET_ID = "T-10003"


def _load_ticket(ticket_id: str) -> dict:
    raw = json.loads(Path(settings.tickets_json).read_text(encoding="utf-8"))
    for t in raw:
        if t["ticket_id"] == ticket_id:
            return t
    raise SystemExit(f"Ticket {ticket_id!r} not in tickets.json")


def main() -> None:
    ticket = _load_ticket(DEMO_TICKET_ID)
    console.print(Panel.fit(
        f"[bold cyan]{ticket['ticket_id']}[/]  ·  {ticket['customer_name']}  "
        f"·  {ticket['customer_region']}\n\n"
        f"[dim]Subject:[/] {ticket['subject']}\n"
        f"[dim]Body:[/] {ticket['body']}\n\n"
        "[yellow]This ticket is engineered to require self-correction:[/]\n"
        "[yellow]→ Primary fix needs HUA-AC-ISO-V2, which is discontinued.[/]\n"
        "[yellow]→ The agent must loop back and find the firmware workaround.[/]",
        title="DEMO · Self-correction trace",
        border_style="cyan",
    ))

    tree = Tree("[bold]Workflow trace[/]")
    seen_nodes: list[str] = []
    final_plan = None

    for node, partial in triage_ticket_stream(ticket):
        seen_nodes.append(node)
        branch = tree.add(f"[bold magenta]{node}[/]  (step {len(seen_nodes)})")
        for k, v in partial.items():
            if k == "messages":
                continue
            v_str = str(v)
            if len(v_str) > 200:
                v_str = v_str[:200] + "…"
            branch.add(f"[dim]{k}[/] = {v_str}")
        if "plan" in partial:
            final_plan = partial["plan"]

    console.print(tree)
    console.rule("[bold green]Self-correction summary")

    research_iterations = sum(1 for n in seen_nodes if n == "research")
    inventory_passes = sum(1 for n in seen_nodes if n == "inventory")
    console.print(
        f"[bold]Research iterations:[/] {research_iterations}  "
        f"[bold]Inventory passes:[/] {inventory_passes}"
    )
    if research_iterations > 1:
        console.print(
            "[bold green]✓ Agent self-corrected[/] — "
            "first attempt hit a discontinued part, second attempt found a workaround."
        )
    else:
        console.print(
            "[yellow]Agent did not loop. "
            "Check that HUA-AC-ISO-V2 is marked 'discontinued' in inventory.csv "
            "and that known_issues.md still includes the HUA-602 entry.[/]"
        )

    if final_plan:
        console.rule("[bold]Final plan (would go to dispatcher dashboard)")
        console.print_json(data=final_plan)


if __name__ == "__main__":
    main()
