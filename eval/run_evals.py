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


def _check_retrieval_hit_rate(case: dict, final: dict) -> tuple[bool | None, str]:
    """Hit Rate@5 for the retrieval step, evaluated independently of triage/extraction.

    Ground truth: the case's expected_error_code. "Hit" means that code appears among
    the error_code metadata of the chunks retrieved on the first (diagnostic) research
    pass (top_k=5) — regardless of what the downstream part-extraction regex later did
    with them. Cases without an expected_error_code (billing/reschedule/no-KB-entry
    tickets) have nothing to retrieve, so they're marked n/a rather than scored.
    """
    want = case.get("expected_error_code")
    if not want:
        return None, "n/a (no expected_error_code)"
    want = want.lower()
    retrieved = [c.lower() for c in (final.get("retrieved_error_codes") or [])]
    hit = any(want in r or r in want for r in retrieved)
    return hit, f"retrieved={retrieved or []}"


def _check_precision_at_1(case: dict, final: dict) -> tuple[bool | None, str]:
    """Precision@1: is the single hit the pipeline actually acted on (top_hit) correct —
    not just present somewhere in the top-5.

    A case can pass Hit Rate@5 (right chunk was retrieved) while failing this (a wrong
    chunk outranked it and got selected instead) — that gap is exactly what this metric
    is for. Same n/a rule as Hit Rate@5: only scored when a ground-truth code exists.
    """
    want = case.get("expected_error_code")
    if not want:
        return None, "n/a (no expected_error_code)"
    want = want.lower()
    selected = (final.get("selected_error_code") or "").lower()
    hit = bool(selected) and (want in selected or selected in want)
    return hit, f"selected={selected or None}"


def main() -> None:
    console = Console()
    cases = json.loads(Path("eval/eval_dataset.json").read_text(encoding="utf-8"))
    tickets = _load_tickets()

    table = Table(title=f"Eval — {len(cases)} cases", show_lines=False)
    table.add_column("ticket")
    table.add_column("task", justify="center")
    table.add_column("retrieval@5", justify="center")
    table.add_column("precision@1", justify="center")
    table.add_column("notes")

    passed = 0
    retrieval_applicable = 0
    retrieval_hits = 0
    precision_applicable = 0
    precision_hits = 0
    for case in cases:
        ticket = tickets.get(case["ticket_id"])
        if not ticket:
            table.add_row(case["ticket_id"], "[red]MISSING[/]", "-", "-", "ticket not in tickets.json")
            continue
        try:
            final = triage_ticket(ticket)
        except Exception as e:
            table.add_row(case["ticket_id"], "[red]ERROR[/]", "-", "-", str(e)[:80])
            continue

        ok, fails = _check(case, final)
        hit, hit_detail = _check_retrieval_hit_rate(case, final)
        if hit is not None:
            retrieval_applicable += 1
            retrieval_hits += int(hit)
        retrieval_cell = "n/a" if hit is None else ("[green]✓[/]" if hit else "[red]✗[/]")

        prec, prec_detail = _check_precision_at_1(case, final)
        if prec is not None:
            precision_applicable += 1
            precision_hits += int(prec)
        precision_cell = "n/a" if prec is None else ("[green]✓[/]" if prec else "[red]✗[/]")

        if ok:
            passed += 1
            task_cell = "[green]✓[/]"
            notes = case.get("notes", "")
        else:
            task_cell = "[red]✗[/]"
            notes = " | ".join(fails)
        if hit is False:
            notes = f"{notes} | retrieval miss: {hit_detail}".strip(" |")
        if prec is False:
            notes = f"{notes} | precision@1 miss: {prec_detail}".strip(" |")
        table.add_row(case["ticket_id"], task_cell, retrieval_cell, precision_cell, notes)

    console.print(table)
    color = "green" if passed == len(cases) else "yellow"
    console.rule(f"[bold {color}]Task Success Rate: {passed}/{len(cases)} passed[/]")
    if retrieval_applicable:
        rcolor = "green" if retrieval_hits == retrieval_applicable else "yellow"
        console.rule(
            f"[bold {rcolor}]Retrieval Hit Rate@5: {retrieval_hits}/{retrieval_applicable} "
            f"({retrieval_applicable} of {len(cases)} cases have a ground-truth error code)[/]"
        )
    if precision_applicable:
        pcolor = "green" if precision_hits == precision_applicable else "yellow"
        console.rule(
            f"[bold {pcolor}]Precision@1: {precision_hits}/{precision_applicable} "
            f"({precision_applicable} of {len(cases)} cases have a ground-truth error code)[/]"
        )


if __name__ == "__main__":
    main()
