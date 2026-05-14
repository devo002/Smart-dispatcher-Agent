"""LangGraph wiring."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import (
    after_inventory,
    after_triage,
    decision_node,
    inventory_node,
    research_node,
    routing_node,
    schedule_node,
    triage_node,
)
from .state import DispatchState


def build_workflow():
    g = StateGraph(DispatchState)

    g.add_node("triage", triage_node)
    g.add_node("research", research_node)
    g.add_node("inventory", inventory_node)
    g.add_node("routing", routing_node)
    g.add_node("decision", decision_node)
    g.add_node("schedule", schedule_node)

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", after_triage, {"research": "research", "decision": "decision"})
    g.add_edge("research", "inventory")
    g.add_conditional_edges(
        "inventory",
        after_inventory,
        {"research": "research", "routing": "routing"},
    )
    g.add_edge("routing", "decision")
    g.add_edge("decision", "schedule")
    g.add_edge("schedule", END)

    return g.compile()
