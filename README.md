# Empire Smart-Dispatcher

An agentic AI system that triages solar inverter and heat pump support tickets, looks up
fixes in technical manuals (RAG), checks spare-part availability, routes the right
technician, and proposes a dispatch plan for human approval.

Built to demonstrate end-to-end ownership of an MCP + LangGraph + Claude Agent SDK stack
for a residential energy company workflow.

## Architecture

```
Customer Ticket (DE/EN, messy)
        |
        v
   [Triage Node]   <-- Claude extracts error code + intent
        |
        v
   [Research Node] --> search_manuals  (ChromaDB RAG over PDFs + curated issues)
        |
        v
   [Inventory Node] --> check_inventory (SQLite over synthetic parts CSV)
        |
        v
   [Routing Node] --> find_technician  (skill + region + earliest slot)
        |
        v
   [Decision Node]  --> if all green: propose plan
                        if part OOS: loop back to Research for workaround
                        if no manual hit: escalate
        |
        v
   [Human-in-the-Loop]  <-- dispatcher approves in dashboard
        |
        v
   [Schedule Node] --> schedule_visit  (writes job ticket)
```

## Stack

- **Language:** Python 3.11+
- **Agent:** Claude Agent SDK (`claude-agent-sdk`)
- **Orchestration:** LangGraph
- **Tools exposed via:** Model Context Protocol (FastMCP)
- **RAG:** ChromaDB + sentence-transformers
- **API:** FastAPI
- **Observability:** LangSmith traces
- **Data:** SQLite + CSV (synthetic) + real inverter PDFs

## Project layout

```
Mcp/
├── data/                       # Knowledge base + synthetic data
│   ├── manuals/                # Drop real PDF inverter/heat-pump manuals here
│   ├── known_issues.md         # Curated error code -> fix lookup
│   ├── inventory.csv           # ~80 synthetic spare parts
│   ├── technicians.csv         # Field tech roster (region, certs, availability)
│   └── tickets/tickets.json    # 25 mixed-language messy customer tickets
│
├── src/empire_dispatcher/
│   ├── config.py               # Paths, model names, env loading
│   ├── ingest/                 # PDF chunking + Chroma index build
│   ├── tools/                  # Pure-Python tool implementations
│   ├── mcp_server.py           # FastMCP wrapper exposing tools
│   ├── graph/                  # LangGraph nodes + workflow
│   ├── agent.py                # Claude Agent SDK entry point
│   └── api.py                  # FastAPI HTTP surface
│
├── eval/                       # LangSmith eval dataset + runner
├── scripts/                    # Seed data, demo self-correction trace
└── frontend/                   # (Next.js dashboard — placeholder)
```


## Quick start

```bash
# 1. Create virtualenv and install
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. Configure secrets
create .env and enter the Anthropic API key
#   then edit .env and paste your ANTHROPIC_API_KEY

# 3. Seed synthetic data + build the RAG index
python -m scripts.seed_data
python -m src.empire_dispatcher.ingest.build_index

# 4. Run the API
uvicorn src.empire_dispatcher.api:app --reload

# 5. Try the wow-factor self-correction demo
python -m scripts.demo_self_correction
```

## Why this stack for Empire

- **MCP** standardizes tool access so the same `check_inventory` tool can be reused
  by Claude Desktop, the dispatcher dashboard, or a future Slack bot — not locked
  inside one app.
- **LangGraph** runs the *workflow state machine* (triage → research → routing →
  approval) with explicit checkpoints and self-correction loops.
- **LangSmith** makes every tool call, every retry, every reasoning step inspectable —
  critical for an enterprise rolling out agents in customer-facing flows.


