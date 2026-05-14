"""Tool implementations.

Each tool is a plain Python function with a Pydantic-typed input/output. The MCP
server (`mcp_server.py`) wraps them; the LangGraph nodes can also call them
directly without going through MCP for in-process speed.

`search_manuals` is imported lazily so that lightweight tools (inventory, routing,
scheduling) don't drag in chromadb / sentence-transformers when unused.
"""

from __future__ import annotations

from .check_inventory import CheckInventoryInput, CheckInventoryOutput, check_inventory
from .find_technician import (
    FindTechnicianInput,
    FindTechnicianOutput,
    find_technician,
)
from .schedule_visit import ScheduleVisitInput, ScheduleVisitOutput, schedule_visit
from .search_manuals import SearchManualsInput, SearchManualsOutput, search_manuals


__all__ = [
    "CheckInventoryInput",
    "CheckInventoryOutput",
    "check_inventory",
    "FindTechnicianInput",
    "FindTechnicianOutput",
    "find_technician",
    "ScheduleVisitInput",
    "ScheduleVisitOutput",
    "schedule_visit",
    "SearchManualsInput",
    "SearchManualsOutput",
    "search_manuals",
]
