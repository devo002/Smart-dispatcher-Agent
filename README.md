# Empire Smart-Dispatcher

An agentic AI system that triages solar inverter and heat pump support tickets, looks up
fixes in technical manuals (RAG), checks spare-part availability, routes the right
technician, and proposes a dispatch plan for human approval.

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
   [Inventory Node] --> check_inventory (live Google Sheets read, 3 retries;
                        5-min stale cache, then SQLite/CSV fallback)
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
├── scripts/                    # Seed data, demo self-correction trace, MCP client demo
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

# 5. Run the evaluation suite (17 cases, all should pass)
python -m eval.run_evals
```


## Evaluation hardening — challenges and fixes

Running the 17-case eval suite exposed two retrieval and extraction bugs that required changes and corrections. Both are documented here because they could reflect
real production pitfalls in RAG-based agentic systems.

### Challenge 1 — Semantic embeddings cannot distinguish error codes

**Problem.** The default ChromaDB embedding model (`all-MiniLM-L6-v2`) converts text
into vectors based on *meaning*, not exact tokens. Error codes like `0x0001`, `501`,
and `602` look nearly identical in embedding space. A ticket about Huawei error `0x0001`
(grid loss — no part needed) was consistently retrieving the `HUA-602` KB entry
(grid overvoltage — part required), causing the wrong diagnosis and a false-positive
part requirement.


**Fix.** During indexing (`chunk_pdfs.py` + `build_index.py`) each chunk now has its
error code and manufacturer extracted from the `## HEADING` and stored as ChromaDB
metadata fields. At query time (`search_manuals.py`), when the triage node has
identified an error code, a `where` metadata filter is applied *before* the vector
ranking step — guaranteeing the correct KB entry is returned. If the filter yields no
results, it falls back to unfiltered vector search so vague tickets (no error code) still
work.
In summary, fixed a retrieval problem by ensuring the AI retrieves the correct knowledge base entry using exact metadata (manufacturer and error code) before semantic ranking.


### Challenge 2 — Fallback SKU scanner extracted conditional parts as required parts

**Problem.** The part-extraction logic in `_extract_part_id` ran two checks: first a
targeted `Required part: <SKU>` pattern, then a looser fallback that scanned the entire
chunk text for any SKU-shaped token. KB entries that say *"Required part: None initially;
`EV-RCD-TYPE-B` if RCD is faulty"* correctly failed the first check, but the fallback
then picked up `EV-RCD-TYPE-B` and returned it as a required part — causing false
positives for the EV wallbox ticket (T-10010) and the smoke-smell ticket (T-10016).

**Fix.** A `_REQUIRED_PART_NONE_PATTERN` guard was added at the top of
`_extract_part_id`. If the chunk explicitly states `Required part: None [...]`, the
function returns `None` immediately and the fallback SKU scan never runs. This covers
both the "None." case (definitive no-part) and the "None initially; ..." case
(conditional parts that should not be pre-ordered without an on-site assessment).
In summary fixed an information extraction problem by preventing the AI from treating conditional or optional replacement parts as mandatory.


### Challenge 3 — No retry/fallback resilience on the Google Sheets inventory read

**Problem.** `check_inventory`'s live Google Sheets backend (`_fetch_from_sheets`)
wrapped the `gspread` call in a bare `except Exception: return None`. Any failure —
a network blip, a Sheets API rate limit, a transient auth hiccup — dropped straight
to the SQLite mirror on the very first try, with no retry. The SQLite mirror is only
rebuilt from `inventory.csv` when that file changes, so during a live outage the
agent would silently start quoting stock levels from a stale snapshot instead of the
live sheet, with no signal that a fallback had occurred.

**Fix.** `_fetch_from_sheets` now retries the live call up to **3 attempts**, with a
short backoff (0.5s, then 1.5s) between failures, before giving up. If all 3 attempts
fail, it serves the last successfully-fetched rows if they're **under 5 minutes old**
(`_SHEET_STALE_TTL`) instead of falling straight to SQLite/CSV. This is a separate,
longer window from the normal-path cache (`_SHEET_CACHE_TTL`, still 30s) used on the
happy path to avoid hitting the Sheets API on every lookup — so live sheet edits still
surface within 30s under normal operation, and only a genuine outage triggers the
5-minute stale-serve grace period before falling back to the CSV mirror.


---

## Why this stack for Empire

- **MCP** standardizes tool access so the same `check_inventory` tool can be reused
  by Claude Desktop, the dispatcher dashboard, or a future Slack bot — not locked
  inside one app.
- **LangGraph** runs the *workflow state machine* (triage → research → routing →
  approval) with explicit checkpoints and self-correction loops.
- **LangSmith** makes every tool call, every retry, every reasoning step inspectable —
  critical for an enterprise rolling out agents in customer-facing flows.


