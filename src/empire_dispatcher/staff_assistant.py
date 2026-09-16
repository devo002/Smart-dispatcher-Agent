"""Free-form support-staff assistant.

Unlike the ticket-triage LangGraph pipeline (a fixed node sequence where tools are
called by hardcoded Python logic), this lets Claude decide which lookup tool(s) to
call — zero, one, or several — based on a free-text staff question. It connects to
`mcp_server.py` as a real MCP client (in-process, via fastmcp.Client), so the exact
same tool definitions that would serve Claude Desktop or any other MCP client are
what drive this too.

schedule_visit is deliberately excluded from the tools offered here: this assistant
answers ad-hoc lookup questions (stock, fixes, technicians), it does not dispatch
jobs — that stays inside the approved triage -> human-approval flow.
"""

from __future__ import annotations

import json

from fastmcp import Client
from pydantic import BaseModel

from .config import settings
from .graph.nodes import _client as _anthropic_client
from .mcp_server import mcp

MAX_TOOL_ROUNDS = 5  # guard against a runaway tool-call loop
_EXCLUDED_TOOLS = {"schedule_visit_tool"}

_ASSISTANT_SYSTEM = """You are an internal assistant for Empire's support staff.
Answer their question using the available tools as needed — call zero, one, or
several tools depending on what's actually being asked (e.g. a question about both
a fix and a technician needs two tool calls). Be concise and cite concrete facts
from tool results (stock levels, technician names, part IDs, error codes) instead
of vague summaries. If a tool call fails, say so plainly rather than guessing.
"""


class ToolCallLog(BaseModel):
    name: str
    arguments: dict
    result: dict | list | str | None = None
    error: str | None = None


class AssistantAnswer(BaseModel):
    answer: str
    tool_calls: list[ToolCallLog]


async def _tools_schema(mcp_client: Client) -> list[dict]:
    tools = await mcp_client.list_tools()
    return [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in tools
        if t.name not in _EXCLUDED_TOOLS
    ]


async def ask_assistant(question: str) -> AssistantAnswer:
    llm = _anthropic_client()
    tool_log: list[ToolCallLog] = []

    async with Client(mcp) as mcp_client:
        tools = await _tools_schema(mcp_client)
        messages: list[dict] = [{"role": "user", "content": question}]

        for _ in range(MAX_TOOL_ROUNDS):
            msg = llm.messages.create(
                model=settings.claude_model,
                max_tokens=800,
                system=_ASSISTANT_SYSTEM,
                messages=messages,
                tools=tools,
            )
            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            if not tool_uses:
                text = next((b.text for b in msg.content if b.type == "text"), "").strip()
                return AssistantAnswer(answer=text or "(no answer produced)", tool_calls=tool_log)

            messages.append({"role": "assistant", "content": msg.content})
            tool_results = []
            for call in tool_uses:
                try:
                    result = await mcp_client.call_tool(call.name, call.input)
                    tool_log.append(ToolCallLog(name=call.name, arguments=call.input, result=result.data))
                    content = json.dumps(result.data, default=str)
                    is_error = False
                except Exception as e:  # tool call failure -> tell Claude, don't crash the assistant
                    tool_log.append(ToolCallLog(name=call.name, arguments=call.input, error=str(e)))
                    content = f"Tool call failed: {e}"
                    is_error = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": content,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        return AssistantAnswer(
            answer="Wasn't able to finish looking this up — it needed more tool calls than allowed. Try a narrower question.",
            tool_calls=tool_log,
        )
