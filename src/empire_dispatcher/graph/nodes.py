"""LangGraph node implementations.

Each node is a pure function from state -> partial state. Where Claude is needed
(triage, plan synthesis), we call the Anthropic SDK directly with structured-output
prompting; everything else is deterministic Python so it's testable without an API key.
"""

from __future__ import annotations

import json
import re
from datetime import date

from anthropic import Anthropic
from langchain_core.messages import AIMessage, HumanMessage

from ..config import settings
from ..tools import (
    CheckInventoryInput,
    FindTechnicianInput,
    ScheduleVisitInput,
    SearchManualsInput,
    check_inventory,
    find_technician,
    schedule_visit,
    search_manuals,
)
from .state import DispatchState


MAX_RESEARCH_ITERATIONS = 2

_TRIAGE_SYSTEM = """You are the triage assistant for Empire, a German residential solar
+ heat-pump company. Your job is to read a (possibly messy, mixed German/English)
customer ticket and extract a structured diagnosis.

Return STRICT JSON with these keys (no prose, no code fences):
{
  "error_code": string | null,
  "manufacturer": string | null,
  "severity": "low" | "medium" | "high",
  "intent": "fault" | "billing" | "reschedule" | "general",
  "required_skills": string[],
  "detected_language": "de" | "en"
}

Rules:
- "high" severity = customer has no power, no heat, smoke smell, or safety concern.
- "medium" = degraded operation, intermittent fault.
- "low" = informational / billing / reschedule.
- If the message is purely administrative (rescheduling, billing question), use intent != "fault".
- Output JSON only.
"""


def _client() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in: {text!r}")
    return json.loads(m.group(0))


def triage_node(state: DispatchState) -> DispatchState:
    ticket = (
        f"Customer: {state.get('customer_name','?')}\n"
        f"Region: {state.get('customer_region','?')}\n"
        f"Subject: {state.get('raw_subject','')}\n"
        f"Body: {state.get('raw_body','')}\n"
    )
    if not settings.anthropic_api_key:
        body = (state.get("raw_body","") + " " + state.get("raw_subject","")).lower()
        guess_code = None
        for pat in ["error 0x0001", "0x0001", "error 501", "501", "error 602", "602",
                    "error 301", "301", "warning 410", "410", "f.22", "a9", "1010", "115"]:
            if pat in body:
                guess_code = pat.upper().replace("ERROR ", "").replace("WARNING ", "")
                break
        return {
            "error_code": guess_code,
            "manufacturer": None,
            "severity": "medium",
            "intent": "fault" if guess_code else "general",
            "required_skills": ["solar"],
            "detected_language": "de" if any(w in body for w in ["und","ist","nicht","wir","heute"]) else "en",
            "iteration": 0,
            "messages": [HumanMessage(content=f"Triage (offline mode) for ticket {state.get('ticket_id')}.")],
        }
    msg = _client().messages.create(
        model=settings.claude_model,
        max_tokens=500,
        system=_TRIAGE_SYSTEM,
        messages=[{"role": "user", "content": ticket}],
    )
    raw = msg.content[0].text if msg.content else "{}"
    try:
        data = _extract_json(raw)
    except Exception:
        data = {}
    return {
        "error_code": data.get("error_code"),
        "manufacturer": data.get("manufacturer"),
        "severity": data.get("severity") or "medium",
        "intent": data.get("intent") or "general",
        "required_skills": data.get("required_skills") or [],
        "detected_language": data.get("detected_language") or "de",
        "iteration": 0,
        "messages": [AIMessage(content=f"Triage: {raw}")],
    }


_ISSUE_CODE_PATTERNS = [
    re.compile(r"^[A-Z]{2,4}-\d{2,4}$"),
    re.compile(r"^HP-[A-Z]+-[A-Z]?\d+(?:\.\d+)?$"),
    re.compile(r"^BAT-[A-Z]{2,4}-[A-Z]{2,3}$"),
    re.compile(r"^MISC-[A-Z]+-[A-Z]+$"),        # MISC-SMOKE-SMELL, MISC-SPD-TRIP
    re.compile(r"^EV-[A-Z]+-[A-Z]+$"),           # EV-WALLBOX-FAULT
]
_SKU_PATTERN = re.compile(r"`?([A-Z][A-Z0-9]{1,4}-[A-Z0-9\-]{2,30})`?")
_REQUIRED_PART_PATTERN = re.compile(
    r"[Rr]equired\s+part[^a-zA-Z\n]{0,15}([A-Z][A-Z0-9]{1,4}-[A-Z0-9\-]{2,30})"
)
_REQUIRED_PART_NONE_PATTERN = re.compile(
    r"[Rr]equired\s+part[^a-zA-Z\n]{0,15}[Nn]one"
)
_PREAMBLE_MARKERS = ("NOTE FOR REVIEWERS", "RAG corpus", "intentionally")


def _looks_like_issue_code(token: str) -> bool:
    return any(p.match(token) for p in _ISSUE_CODE_PATTERNS)


def _extract_part_id(blob: str):
    if not blob:
        return None
    # KB explicitly says "Required part: None [...]" — stop here, no part needed.
    if _REQUIRED_PART_NONE_PATTERN.search(blob):
        return None
    m = _REQUIRED_PART_PATTERN.search(blob)
    if m:
        candidate = m.group(1).rstrip("-.,;: ")
        if not _looks_like_issue_code(candidate):
            return candidate
    for m in _SKU_PATTERN.finditer(blob):
        token = m.group(1).rstrip("-.,;: ")
        if _looks_like_issue_code(token):
            continue
        return token
    return None


def _looks_like_workaround(text: str) -> bool:
    if any(marker in text for marker in _PREAMBLE_MARKERS):
        return False
    lower = text.lower()
    if "## workarounds" in lower:
        return True
    if re.search(r"###\s*[A-Z0-9\.\-]+\s*fallback", text, re.IGNORECASE):
        return True
    if "firmware" in lower and ("procedure" in lower or "version" in lower or "push" in lower):
        return True
    return False


def _conditional_workaround(state):
    """Only surface a workaround when the part is truly unavailable.

    Rule: workaround appears only when the inventory check identified the right
    part and that part is discontinued or on backorder. If the part was never
    identified (status='missing'/'unknown') or is in stock, no workaround is shown.
    """
    workaround = state.get("workaround")
    if not workaround:
        return None
    if state.get("part_in_stock"):
        return None
    if state.get("part_status") not in ("discontinued", "backorder"):
        return None
    return workaround


def research_node(state: DispatchState) -> DispatchState:
    iteration = (state.get("iteration") or 0) + 1
    base_query_parts = [
        state.get("error_code") or "",
        state.get("manufacturer") or "",
        state.get("raw_subject") or "",
    ]
    query = " ".join(p for p in base_query_parts if p).strip()
    # When there is no error code or manufacturer, the subject alone is too vague for
    # reliable retrieval — include the body to give the embedding model richer signal.
    if not (state.get("error_code") or state.get("manufacturer")):
        query = f"{query} {state.get('raw_body', '')[:200]}".strip()
    if iteration > 1:
        query = f"workaround firmware fallback alternative fix for {query}"
    result = search_manuals(SearchManualsInput(
        query=query,
        top_k=5,
        error_code=state.get("error_code") if iteration == 1 else None,
    ))
    # Use only the top non-workaround hit for part extraction so that lower-ranked
    # hits from unrelated issues cannot pollute the result.
    top_hit = next((h for h in result.hits if not _looks_like_workaround(h.text)), None) or (result.hits[0] if result.hits else None)
    required_part_id = _extract_part_id(top_hit.text if top_hit else "")
    workaround_text = None
    if iteration > 1:
        for h in result.hits:
            if _looks_like_workaround(h.text):
                workaround_text = h.text[:500]
                break
    if iteration == 1:
        if required_part_id:
            fix_hit = (
                next((h for h in result.hits if required_part_id in h.text), None)
                or next((h for h in result.hits if not _looks_like_workaround(h.text)), None)
                or (result.hits[0] if result.hits else None)
            )
        else:
            fix_hit = next((h for h in result.hits if not _looks_like_workaround(h.text)), None) or (result.hits[0] if result.hits else None)
    else:
        fix_hit = result.hits[0] if result.hits else None
    candidate_fix = fix_hit.text[:1500] if fix_hit else None
    return {
        "iteration": iteration,
        "candidate_fix": candidate_fix,
        "required_part_id": required_part_id if iteration == 1 else state.get("required_part_id"),
        "workaround": workaround_text or state.get("workaround"),
        "messages": [AIMessage(content=f"Research[{iteration}] query={query!r} hits={len(result.hits)}")],
    }


def inventory_node(state: DispatchState) -> DispatchState:
    part_id = state.get("required_part_id")
    if not part_id:
        return {
            "part_in_stock": False,
            "part_status": "unknown",
            "inventory_summary": "No part identified by research; nothing to check.",
            "messages": [AIMessage(content="Inventory: no part_id from research, skipping lookup.")],
        }
    out = check_inventory(CheckInventoryInput(part_id=part_id, region=state.get("customer_region")))
    top = out.matches[0] if out.matches else None
    return {
        "part_in_stock": bool(top and top.in_stock),
        "part_status": top.status if top else "missing",
        "inventory_summary": out.summary,
        "messages": [AIMessage(content=f"Inventory: {out.summary}")],
    }


def routing_node(state: DispatchState) -> DispatchState:
    out = find_technician(
        FindTechnicianInput(
            region=state.get("customer_region", "Berlin"),
            required_skills=state.get("required_skills") or ["solar"],
            required_certifications=[state.get("manufacturer")] if state.get("manufacturer") else [],
            preferred_languages=[state.get("detected_language") or "de"],
            by_date=date.today().isoformat(),
        )
    )
    top = out.matches[0] if out.matches else None
    backups = [m.model_dump() for m in out.matches[1:3]]
    suffix = f" ({len(backups)} backup{'s' if len(backups) != 1 else ''} stashed)"
    return {
        "chosen_tech_id": top.tech_id if top else None,
        "chosen_tech_name": top.name if top else None,
        "chosen_tech_region": top.region if top else None,
        "scheduled_date": top.next_available if top else None,
        "routing_summary": out.summary,
        "alternative_techs": backups,
        "messages": [AIMessage(content=f"Routing: {out.summary}{suffix}")],
    }


def decision_node(state: DispatchState) -> DispatchState:
    plan = {
        "ticket_id": state.get("ticket_id"),
        "diagnosis": {
            "error_code": state.get("error_code"),
            "manufacturer": state.get("manufacturer"),
            "severity": state.get("severity"),
            "summary": (state.get("candidate_fix") or "")[:1500],
        },
        "part": {
            "part_id": state.get("required_part_id"),
            "in_stock": state.get("part_in_stock"),
            "status": state.get("part_status"),
            "inventory_note": state.get("inventory_summary"),
        },
        "workaround": _conditional_workaround(state),
        "technician": {
            "tech_id": state.get("chosen_tech_id"),
            "name": state.get("chosen_tech_name"),
            "region": state.get("chosen_tech_region"),
            "scheduled_date": state.get("scheduled_date"),
            "rationale": state.get("routing_summary"),
        },
        "backup_technicians": state.get("alternative_techs") or [],
        "iterations_used": state.get("iteration", 0),
    }
    return {
        "plan": plan,
        "needs_human_approval": True,
        "escalated": state.get("intent") != "fault",
        "messages": [AIMessage(content="Plan composed. Awaiting dispatcher approval.")],
    }


def schedule_node(state: DispatchState) -> DispatchState:
    plan = dict(state.get("plan") or {})
    tech_id = (plan.get("technician") or {}).get("tech_id")
    sched = (plan.get("technician") or {}).get("scheduled_date") or date.today().isoformat()
    if not tech_id:
        plan["job_id"] = None
        plan["job_status"] = "no_dispatch"
        return {
            "plan": plan,
            "messages": [AIMessage(content="Schedule skipped: no technician selected.")],
        }
    out = schedule_visit(
        ScheduleVisitInput(
            ticket_id=state.get("ticket_id", "T-UNKNOWN"),
            tech_id=tech_id,
            scheduled_date=sched,
            diagnosis=(plan.get("diagnosis") or {}).get("summary", ""),
            parts_to_bring=[plan.get("part", {}).get("part_id")] if plan.get("part", {}).get("in_stock") else [],
            workaround_applied=plan.get("workaround"),
            requires_approval=True,
        )
    )
    plan["job_id"] = out.job_id
    plan["job_status"] = out.status
    return {
        "plan": plan,
        "messages": [AIMessage(content=f"Schedule: {out.summary}")],
    }


def after_inventory(state: DispatchState) -> str:
    if state.get("part_in_stock"):
        return "routing"
    if (state.get("iteration") or 0) >= MAX_RESEARCH_ITERATIONS:
        return "routing"
    return "research"


def after_triage(state: DispatchState) -> str:
    if state.get("intent") != "fault":
        return "decision"
    return "research"
