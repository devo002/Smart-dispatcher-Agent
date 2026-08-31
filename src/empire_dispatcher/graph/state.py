"""Shared state passed between LangGraph nodes."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages


class DispatchState(TypedDict, total=False):
    # --- Input ---
    ticket_id: str
    customer_name: str
    customer_region: str
    raw_subject: str
    raw_body: str
    detected_language: str  # 'de' | 'en'

    # --- Triage output ---
    error_code: str | None        # e.g. 'HUA-602', 'F.22', '301'
    manufacturer: str | None      # 'Huawei' | 'SMA' | 'Vaillant' | ...
    severity: Literal["low", "medium", "high"] | None
    intent: str                   # 'fault', 'billing', 'reschedule', 'general'

    # --- Research output ---
    candidate_fix: str | None
    required_part_id: str | None
    required_skills: list[str]
    retrieved_error_codes: list[str]  # error_code metadata of iteration-1 hits; ground truth source for retrieval Hit Rate@k eval
    selected_error_code: str | None   # error_code of the single hit the pipeline actually acted on (top_hit); ground truth source for Precision@1 eval

    # --- Inventory output ---
    part_in_stock: bool | None
    part_status: str | None       # 'active' | 'discontinued' | ...
    inventory_summary: str | None

    # --- Workaround (after self-correction) ---
    workaround: str | None

    # --- Routing output ---
    chosen_tech_id: str | None
    chosen_tech_name: str | None
    chosen_tech_region: str | None
    scheduled_date: str | None
    routing_summary: str | None
    alternative_techs: list[dict]  # backups: top 2-3 candidates after primary

    # --- Decision / output ---
    plan: dict[str, Any] | None
    needs_human_approval: bool
    escalated: bool
    iteration: int                # how many research<->inventory loops we've done

    # --- Telemetry ---
    messages: Annotated[list, add_messages]
