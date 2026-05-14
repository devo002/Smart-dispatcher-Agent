"""FastMCP server exposing the four dispatcher tools.

Run as a stdio MCP server (e.g. wired into Claude Desktop config) with:

    python -m src.enpal_dispatcher.mcp_server
"""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import (
    CheckInventoryInput,
    FindTechnicianInput,
    ScheduleVisitInput,
    SearchManualsInput,
    check_inventory,
    find_technician,
    schedule_visit,
    search_manuals,
)


mcp = FastMCP("empire-smart-dispatcher")


@mcp.tool(
    description=(
        "Search Empire's manuals + curated known-issues knowledge base for relevant fixes. "
        "Pass the customer symptom and any error codes you've extracted."
    )
)
def search_manuals_tool(query: str, top_k: int = 4) -> dict:
    out = search_manuals(SearchManualsInput(query=query, top_k=top_k))
    return out.model_dump()


@mcp.tool(
    description=(
        "Look up a spare part in inventory. Use part_id when you know the SKU "
        "(e.g. 'HUA-AC-ISO-V2'), otherwise pass a free-text query. "
        "Optionally pass region to prefer the closest warehouse."
    )
)
def check_inventory_tool(
    part_id: str | None = None,
    query: str | None = None,
    region: str | None = None,
) -> dict:
    out = check_inventory(
        CheckInventoryInput(part_id=part_id, query=query, region=region)
    )
    return out.model_dump()


@mcp.tool(
    description=(
        "Find the best field technician given the customer region, the skills required "
        "(e.g. 'solar', 'heatpump', 'refrigerant'), and any vendor certifications "
        "the job needs (e.g. 'Huawei', 'Vaillant')."
    )
)
def find_technician_tool(
    region: str,
    required_skills: list[str] | None = None,
    required_certifications: list[str] | None = None,
    preferred_languages: list[str] | None = None,
    by_date: str | None = None,
) -> dict:
    out = find_technician(
        FindTechnicianInput(
            region=region,
            required_skills=required_skills or [],
            required_certifications=required_certifications or [],
            preferred_languages=preferred_languages or ["de"],
            by_date=by_date,
        )
    )
    return out.model_dump()


@mcp.tool(
    description=(
        "Schedule a field visit. By default the job is queued as 'pending_approval' "
        "for a human dispatcher to confirm before the technician is dispatched."
    )
)
def schedule_visit_tool(
    ticket_id: str,
    tech_id: str,
    scheduled_date: str,
    diagnosis: str,
    parts_to_bring: list[str] | None = None,
    workaround_applied: str | None = None,
    requires_approval: bool = True,
) -> dict:
    out = schedule_visit(
        ScheduleVisitInput(
            ticket_id=ticket_id,
            tech_id=tech_id,
            scheduled_date=scheduled_date,
            diagnosis=diagnosis,
            parts_to_bring=parts_to_bring or [],
            workaround_applied=workaround_applied,
            requires_approval=requires_approval,
        )
    )
    return out.model_dump()


if __name__ == "__main__":
    mcp.run()
