"""Run the workflow against the eval dataset and report pass/fail per case.

Doubles as a LangSmith dataset uploader — set LANGSMITH_TRACING=true and the
runs are auto-traced; you can then turn the results into an eval dataset in
the LangSmith UI.

    python -m eval.run_evals
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.empire_dispatcher.agent import triage_ticket
from src.empire_dispatcher.config import settings


SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _load_tickets() -> dict[str, dict]:
    raw = json.loads(Path(settings.tickets_json).read_text(encoding="utf-8"))
    return {t["ticket_id"]: t for t in raw}


def _check(case: dict, final: dict) -> tuple[bool, list[str]]:
    fails: list[str] = []
    plan = final.get("plan") or {}
    diag = plan.get("diagnosis") or {}

    if "expected_error_code" in case:
        got = (diag.get("error_code") or "").upper()
        want = case["expected_error_code"].upper()
        if want not in got and got not in want:
            fails.append(f"error_code: got {got!r}, want substring of {want!r}")

    if "expected_manufacturer" in case:
        got = (diag.get("manufacturer") or "").lower()
        want = case["expected_manufacturer"].lower()
        if want not in got:
            fails.append(f"manufacturer: got {got!r}, want {want!r}")

    if "expected_severity_min" in case:
        got = (diag.get("severity") or "low").lower()
        want_rank = SEVERITY_RANK[case["expected_severity_min"]]
        got_rank = SEVERITY_RANK.get(got, 0)
        if got_rank < want_rank:
            fails.append(f"severity: got {got!r} (rank {got_rank}), want >= {case['expected_severity_min']!r}")

    if "should_require_part" in case:
        has_part = bool((plan.get("part") or {}).get("part_id"))
        if has_part != case["should_require_part"]:
            fails.append(f"required_part: got {has_part}, want {case['should_require_part']}")

    if case.get("should_self_correct") and final.get("iteration", 0) <= 1:
        fails.append("expected self-correction loop (iteration > 1) but graph ran straight through")

    return (len(fails) == 0, fails)


def main() -> None:
    console = Console()
    cases = json.loads(Path("eval/eval_dataset.json").read_text(encoding="utf-8"))
    tickets = _load_tickets()

    table = Table(title=f"Eval — {len(cases)} cases", show_lines=False)
    table.add_column("ticket")
    table.add_column("pass", justify="center")
    table.add_column("notes")

    passed = 0
    for case in cases:
        ticket = tickets.get(case["ticket_id"])
        if not ticket:
            table.add_row(case["ticket_id"], "[red]MISSING[/]", "ticket not in tickets.json")
            continue
        try:
            final = triage_ticket(ticket)
        except Exception as e:
            table.add_row(case["ticket_id"], "[red]ERROR[/]", str(e)[:80])
            continue
        ok, fails = _check(case, final)
        if ok:
            passed += 1
            table.add_row(case["ticket_id"], "[green]✓[/]", case.get("notes", ""))
        else:
            table.add_row(case["ticket_id"], "[red]✗[/]", " | ".join(fails))

    console.print(table)
    color = "green" if passed == len(cases) else "yellow"
    console.rule(f"[bold {color}]{passed}/{len(cases)} passed[/]")


if __name__ == "__main__":
    main()
