# Empire Smart-Dispatcher — Interview Prep

A reference doc for talking through this project and answering applied
problem-solving questions. Read it once, then re-write everything in your
own voice — interviewers can tell when you're reciting.

---

## Part 1: The project, end to end

### The 30-second pitch

> "I built an agentic AI dispatcher that takes messy customer support tickets in
> German and English, finds the right fix using RAG over a knowledge base,
> checks if the spare part is in stock, picks the best technician — with two
> backup options — and queues a dispatch plan for a human dispatcher to approve.
> The whole thing runs end-to-end with a self-correction loop that recovers
> when the primary fix isn't possible. There's a real React dashboard, a
> FastAPI backend, MCP-exposed tools, and a 17-case eval set."

That's the whole project in one breath. Memorize that.

### The 2-minute pitch

The problem: a residential solar/heat-pump company gets hundreds of support
tickets a day, written by frustrated customers in mixed German and English,
often with the error code buried inside a paragraph of context ("rote LED am
Wechselrichter, Error 602, lange Kabel"). A human dispatcher has to read each
one, look up the error in the manual, check if the right part is in stock,
find an available technician with the right certification in the customer's
region, and book a visit. That's slow, cognitively heavy, and inconsistent
across dispatchers.

The system I built turns that into a structured pipeline with an LLM at the
front and deterministic Python at the back. Claude does the messy part —
reading the email and extracting `error_code`, `manufacturer`, `severity`,
`required_skills`, and `language`. Everything downstream is plain Python:
a vector search over the manual corpus, a SQL lookup on inventory, a scoring
formula on the technician roster. The agent's output is a structured "plan"
that goes to the dispatcher dashboard for approval.

The key trick is the **self-correction loop**: when the primary fix calls
for a part that's discontinued, the graph loops back to the research step
with a different query ("workaround firmware fallback alternative fix for…")
and finds an Empire-approved temporary procedure. That's the headline demo —
it shows the agent recovering from a failure, not just succeeding on the
happy path.

The whole thing is built on **MCP** (Model Context Protocol) so the same
tools can be reused by Claude Desktop, the dashboard, or any future Slack
bot — there's one source of truth for what `check_inventory` does, not four
copies. **LangGraph** orchestrates the workflow as an explicit state machine,
which means every tool call and every retry is inspectable in LangSmith
traces. **Human-in-the-loop** is built into the data layer: jobs are written
as `pending_approval` and never auto-dispatch.

### The architecture in one picture

```
   Messy customer email (DE/EN)
            │
            ▼
        TRIAGE  ────────►  Claude extracts error_code, manufacturer,
            │              severity, intent, required_skills, language
            │
   ┌────────┴────────┐
   ▼                 ▼
NON-FAULT         FAULT
(reschedule,         │
 billing)            ▼
   │            RESEARCH  ────►  ChromaDB RAG over known_issues.md + PDFs
   │                │
   │                ▼
   │            INVENTORY  ────►  SQLite mirror of inventory.csv
   │                │
   │       ┌────────┴───────┐
   │       │                │
   │       ▼ part missing   ▼ part in stock
   │   loop back          ROUTING  ────►  scoring formula across
   │   (max 2x)              │             24 technicians
   │       │                 │
   │       └────────┬────────┘
   │                ▼
   └──────────►  DECISION  ────► assembles plan: diagnosis + part +
                    │             workaround + primary tech + 2 backups
                    ▼
              SCHEDULE  ────►  appends JOB-XXXX to jobs.json
                    │             with status="pending_approval"
                    ▼
            HUMAN-IN-THE-LOOP
                    │
            ┌───────┴────────┐
            ▼                ▼
         APPROVE          REJECT
        ("scheduled")    ("rejected")
```

### What's actually shipping

- **Six LangGraph nodes** (triage, research, inventory, routing, decision,
  schedule), each a pure function from state to state.
- **Two conditional edges** that turn the pipeline into an agent:
  `after_triage` skips the diagnostic chain for non-fault tickets;
  `after_inventory` triggers the self-correction loop when parts are missing.
- **Four pure-Python tools** wrapped as MCP tools via FastMCP:
  `search_manuals` (RAG), `check_inventory` (SQL), `find_technician`
  (scoring), `schedule_visit` (writes the job).
- **A single-file React dashboard** (no npm, no build step — React + Tailwind
  + Babel via CDN) with an inbox, plan card, primary-tech card with region
  badge, backup-tech section, and approve/reject buttons.
- **A FastAPI backend** with eight endpoints serving the UI, the workflow,
  the inventory peek, and the jobs lifecycle.
- **A 17-case eval set** with expected outcomes for triage accuracy, severity
  ranking, part identification, and self-correction behavior.

### Technical decisions worth defending

**Why LangGraph instead of just the Claude Agent SDK?**
LangGraph gives me an explicit state machine I can draw on a whiteboard.
Every node, every edge, every retry shows up as a discrete step in
LangSmith. The Claude Agent SDK is great when you want Claude itself to
drive the loop, but here I want predictable, auditable workflow execution
with one LLM call at a specific step (triage). Different tools for
different shapes of problem.

**Why MCP for tool exposure?**
One source of truth. The same `check_inventory` function can be called
from the LangGraph node, from the FastAPI endpoint, from Claude Desktop,
or from a future Slack integration. Without MCP, I'd be writing four
slightly-different wrappers around the same SQL query.

**Why deterministic Python for inventory and routing?**
Three reasons. First, it's auditable — when a dispatcher asks "why did
you pick this technician?", the answer is a five-line scoring formula, not
"Claude said so." Second, it's free and fast — no LLM call, no tokens. Third,
it's testable — the eval set runs without an API key for half the cases.

**Why a human-in-the-loop checkpoint?**
Field dispatch involves a real technician driving to a real customer's
house. Auto-dispatching from an LLM that occasionally hallucinates a part
ID is a customer-trust disaster waiting to happen. The `pending_approval`
status is enforced at the data layer — even if the UI breaks, no job
becomes `scheduled` without a human action.

**Why ChromaDB for RAG instead of Pinecone or Weaviate?**
Local-first development, no API costs, fast enough for the corpus size, and
the embedding function is swappable if I ever need a multilingual model.
For an enterprise rollout I'd revisit, but for an MVP and demo, ChromaDB
is the lowest-friction choice.

### The wow moments to demo

1. **T-10003 self-correction trace.** The customer reports Error 602 with
   a 30-meter cable run. The agent correctly identifies the primary fix as
   `HUA-AC-ISO-V2`, but inventory says it's discontinued. The graph loops
   back to research, refines the query, surfaces the firmware-update
   workaround from a different section of the same document, and proposes
   a tech who can push the firmware remotely. **Show the trace, not just
   the result.**

2. **The honest debugging story.** When a tester ran T-10006 (Goodwe
   battery offline), the agent grabbed the wrong "part_id" because my
   regex was matching the section header (`GOO-115`) instead of the
   actual SKU. I diagnosed it live, shipped a smarter extractor that
   prefers tokens after `"Required part."` and explicitly excludes
   issue-code shapes, and added a sanity check. The interviewer should
   see that you can debug an agentic system, not just build one.

3. **Backup technicians.** When the algorithm's #1 pick is far from the
   customer, the dispatcher needs to see local alternatives. The plan now
   surfaces top-3 candidates with rationale, so the human can override
   "best on points" with "good enough but local."

### What I'd improve next, honestly

- **Tighten the triage prompt.** Claude occasionally invents skill names
  outside the allowed list. A stricter "ONLY use these exact values, do
  not invent new ones" prompt and a post-validation step would fix this.
- **Add the swap-and-approve action.** Currently backup techs are display-
  only. One click to reassign would make the dashboard production-ready.
- **LangSmith dataset evals.** Right now `eval/run_evals.py` runs
  pass/fail locally. Uploading to a LangSmith dataset would give me
  diff views across model/prompt versions.
- **Real CRM integration.** Replace `data/jobs.json` with a Salesforce
  or Asana write. The `schedule_visit` tool is already isolated, so this
  is a one-file change.
- **Edit-before-approve.** A dispatcher should be able to override the
  diagnosis or the part suggestion before approving, not just accept or
  reject the agent's plan wholesale.

---

## Part 2: Applied questions you'll likely get

These are scenario-style questions that test reasoning, not knowledge.
For each: the question, why they're asking, a structured answer, and one
killer detail to drop.

### Q1. "Your agent dispatched the wrong technician for a customer. How do you debug it?"

**Why they ask:** They want to know if you can investigate a production
issue, not just write happy-path code.

**Answer:**
"First I'd find the LangSmith trace for that ticket — every tool call and
every state transition is logged. I'd look at four things in order: did
triage extract the right structured fields, did research find the right
manual entry, did inventory return what I expected, did routing's scoring
formula score the candidates the way I expected. Most failures are at one
of those four boundaries.

Once I'd localized it, the fix lives in one node, not in the LLM. If
triage was wrong, I tighten the prompt and add a test case to the eval
set. If routing was wrong, I look at whether the scoring weights need
re-balancing — for example, region match was beating language match by
too much in early runs. If research was wrong, it's usually a chunking
or embedding issue, which I'd address in the ingest step.

The thing I want to avoid is whack-a-mole — fixing one ticket at the
expense of regressing five others. So every bug fix gets a corresponding
eval case so I can confirm it stays fixed."

**Killer detail:** Mention that you actually shipped a fix like this for
the GOO-115 → GOO-CAN-CABLE-2M part-id confusion — name it, walk through
it, show you've debugged real agentic systems before.

---

### Q2. "How do you keep this affordable if you scale it to 10,000 tickets a day?"

**Why they ask:** Cost awareness separates engineers from senior engineers.

**Answer:**
"Three levers, in order of impact.

First, I only call the LLM once per ticket — at triage. Everything else
is deterministic Python. So my LLM cost scales linearly with ticket
volume but stays one cheap call per ticket, not five expensive ones.

Second, I'd cache aggressively. A lot of tickets are duplicates or near-
duplicates — 'meine Anlage produziert nicht' shows up dozens of times a
day. A semantic cache on the triage output keyed by the body's embedding
would turn many calls into cache hits. I'd use Claude Haiku for the
triage step too — it's accurate enough for structured extraction at a
fraction of Sonnet's cost, and I'd only escalate to Sonnet for ambiguous
or low-confidence cases.

Third, I'd batch the routing and inventory calls if the dispatch backend
supported it. The agent doesn't need to decide everything in real time —
overnight batching for non-urgent tickets is fine, with same-day routing
only for high-severity cases.

Beyond cost, I'd put rate limits on the public endpoints to protect
against runaway loops, and I'd alert if the LLM call rate per ticket
exceeded one — that means the self-correction loop is firing too often,
which is a quality issue dressed up as a cost issue."

**Killer detail:** "Most teams over-engineer model fallback before they
fix their cache hit rate. I'd start with caching."

---

### Q3. "What if the LLM hallucinates a part ID that doesn't exist?"

**Why they ask:** Hallucination handling is real-world AI engineering.

**Answer:**
"I already have this case. The inventory tool returns
`status: 'unknown'` when the part isn't in the database, which is a
detectable failure state. The graph treats that the same as a stockout —
it loops back to research, refines the query, and looks for a workaround
or alternative.

But the deeper answer is that I don't let Claude invent part IDs in the
first place. The LLM extracts the structured triage data; the part_id is
extracted by a regex from the RAG chunk, not generated by the model. So
the part_id is always grounded in the corpus — if a chunk says
`Required part. \`HUA-AC-ISO-V2\``, that's what becomes the part_id.

If I needed Claude to suggest parts directly, I'd constrain it with a
JSON schema and a validation step that rejects any part_id not in
`inventory.csv`. The model can then re-attempt with the constraint
explicitly in context."

**Killer detail:** "The principle I'd state to the team: never let the
LLM speak about identifiers that exist in your database. Either retrieve
them or validate them."

---

### Q4. "How do you stop the agent from auto-dispatching something dangerous?"

**Why they ask:** Trust and safety in agentic workflows.

**Answer:**
"Defense in depth, three layers.

The first layer is the data model: jobs are written as `pending_approval`,
not `scheduled`. The state of a job in our database literally says 'no
technician is being sent yet.' That's enforced at the SQL level, not just
in code.

The second layer is the UI: the dispatcher has to click Approve in the
dashboard, and the button is disabled if certain conditions aren't met —
for example, if severity is 'high' and no technician was matched, I'd
require a manual override path with a typed reason.

The third layer is severity-aware routing. For high-severity tickets
(no heat with kids in winter, smoke smell, etc.), I'd add a manager-
approval requirement on top of the dispatcher's first approval. Two
people have to sign off before a same-day dispatch on a critical case.

The general principle: the LLM proposes, humans dispose. The agent's job
is to surface a structured recommendation with its reasoning visible, not
to act autonomously on customer-affecting decisions."

**Killer detail:** Reference the actual line in `schedule_visit.py` —
`status="pending_approval" if requires_approval else "scheduled"` — and
note that `requires_approval` defaults to True. Show you've thought about
this at the data layer, not just the UI.

---

### Q5. "We want to extend this to five new product verticals. How would you scale it?"

**Why they ask:** Architecture thinking and team-fit.

**Answer:**
"The architecture supports it well because the LLM is doing the
language-to-structure conversion, and everything else is data. To add a
new vertical I need three things: more knowledge-base entries in
`known_issues.md` (or a new file), an extended `inventory.csv` with the
new parts, and updated `technicians.csv` with the new skill tags and
certs.

The graph itself doesn't change. The triage prompt would need to allow
the new manufacturer values and skill tags, but that's a one-line edit.
The MCP tools stay identical because they're already vertical-agnostic —
they just look up whatever you ask for.

What does change is the eval set. Each new vertical gets 10-20 test
cases with expected outcomes. That's what catches regressions when I
update the triage prompt or the chunker.

Where I'd be careful: as the corpus grows, RAG retrieval gets noisier.
I'd consider per-vertical sub-collections in ChromaDB so a 'Vaillant
heat pump' query doesn't have to compete with 5,000 solar-inverter
chunks for the top-K slots. Filter by metadata at query time, not after."

**Killer detail:** Frame it as "the agent is the boring part — most of
the work is data quality." That's a senior take.

---

### Q6. "How do you ensure quality across multiple languages — German, English, French, Italian?"

**Why they ask:** Real European customer-service problem.

**Answer:**
"Two layers: triage and response.

For triage, Claude is genuinely good at extracting structured data from
mixed-language input — 'rote LED' and 'red LED' both produce the same
`error_code` and `severity`. I detect language inside triage so I can
route to a tech who speaks it. The eval set would need cases in each
language with expected outcomes.

For the response back to the customer, I wouldn't have the agent write
the message at all in v1. The plan is structured data; the customer-
facing response is generated from a localized template per status. That
gives me legal/brand control over what goes out, and it's much easier
to review.

If I needed dynamic responses, I'd generate them in the customer's
language, then have a quick check-back step where Claude verifies the
generated text matches the structured plan before sending. Cheap and
catches most regressions.

The bigger language quality issue is the corpus. If `known_issues.md`
is in English but the customer wrote in German, RAG can still work
because the embedding model is multilingual — but it's noisier. I'd
maintain parallel German/English corpora for any high-volume markets,
not rely solely on multilingual embeddings."

**Killer detail:** "I'd never let the agent write the customer email
in v1 — the plan is structured data, the customer email is a templated
output. That separation is what makes the system safe to ship."

---

### Q7. "A customer is using Zendesk and wants this dispatcher inside their existing ticket flow. How do you integrate?"

**Why they ask:** Integration thinking — most agentic systems live or die
on this.

**Answer:**
"The agent runs server-side and exposes its workflow via the FastAPI
endpoint `/tickets/triage`. So the integration question is: when does
Zendesk call us, and where does the response go?

Three integration points. First, a Zendesk webhook fires on new ticket
creation, hits our `/tickets/triage` endpoint with the ticket body, and
gets back the structured plan. Second, our schedule node writes the
plan back as a private comment on the Zendesk ticket via the Zendesk
API — that way the dispatcher sees it inline, not in a separate
dashboard. Third, the approve/reject action is a Zendesk macro that
calls our `/jobs/{id}/approve` endpoint.

The MCP architecture pays off here: the same tools that power our own
dashboard can be exposed to a Zendesk app or a Slack bot or a Claude
Desktop integration without rewriting anything. I'd also build a thin
Zendesk app for the dispatcher view, but the back-end stays exactly
as-is.

What I'd be careful about: webhook retries and idempotency. If Zendesk
sends the same ticket twice, I shouldn't create two jobs. I'd key on
`ticket_id` and skip if a `pending_approval` job already exists."

**Killer detail:** Walk them through it as a sequence diagram in your
head, not just bullet points. Engineers love precise integration
narratives.

---

### Q8. "How do you know your agent is getting better over time?"

**Why they ask:** Eval discipline. This is the question that separates
hobbyists from production engineers.

**Answer:**
"Three things, in order of importance.

First, an eval set with expected outcomes. I have one — 17 cases right
now, with assertions on error_code extraction, severity ranking, part
identification, and self-correction behavior. Every time I change the
triage prompt or the chunker or the scoring formula, the eval set runs
and I see exactly what regressed and what improved.

Second, regression triggers tied to bug reports. When a real ticket gets
mishandled in production, that ticket plus its expected outcome gets
added to the eval set. Six months later I have a dataset that's
genuinely representative of the failure modes the system actually hits.

Third, online metrics. Approval rate (how often does the dispatcher
approve the agent's plan unchanged?), edit rate (how often do they
edit before approving?), and reject rate. If approval rate is dropping
over a week, something has degraded — either a model change, a corpus
drift, or a new failure mode. Those metrics get logged per ticket and
charted weekly.

I'd resist the temptation to use only LLM-as-judge evals — they're
useful for fluency but they don't catch the 'wrong technician' kind of
mistake. Structured assertions on the plan dict are what I trust."

**Killer detail:** "Approval rate is the metric I'd put on a wall.
Everything else feeds into it."

---

### Q9. "When would you let Claude pick which tool to call vs. hard-coding the workflow like you did?"

**Why they ask:** Architecture judgment.

**Answer:**
"Hard-code the workflow when the steps are predictable and the
business consequences are real. Let the model pick when the path is
genuinely uncertain.

In this project, the path is predictable: every fault ticket needs
triage, then research, then inventory, then routing. There's no value
in letting Claude decide whether to call inventory — it always should.
Hard-coding the graph means lower latency, lower cost, more predictable
behavior, and easier evaluation.

I'd let Claude pick the tool in cases like: an analyst chatbot that
might need to query a database, search docs, or generate a chart
depending on the question. There the path is open-ended and the
business cost of taking a slightly wrong path is low.

The general rule: agency at the edges, determinism in the middle.
Claude reads the unstructured input at the top of the funnel, the
deterministic graph runs the business logic, and Claude can write the
unstructured output at the bottom — but the middle is plain code."

**Killer detail:** "Every time you let an LLM decide a step that you
could have hard-coded, you're paying with latency, money, and
auditability. Sometimes that's the right trade. Often it isn't."

---

### Q10. "Where would you NOT use an agent for a problem like this?"

**Why they ask:** Engineering maturity. Knowing when not to deploy AI is
a senior trait.

**Answer:**
"Three cases I'd avoid.

First, anything where the data is structured and the rules are stable.
'Schedule a recurring maintenance visit every six months' is a cron
job with a CRM call. You don't need an agent. Adding one introduces
non-determinism and cost for zero accuracy gain.

Second, anything regulated. Compliance reporting to the German grid
operator (Netzanmeldung), KYC checks, billing reconciliation — these
need exact, auditable, deterministic logic. An LLM in that loop is
adding regulatory risk for no real upside.

Third, anything where wrong answers are cheap and right answers are
worthless. Internal triage in low-volume queues. Reading press releases
to summarize for a CEO. The signal-to-noise on agent value is too low.

The cases where an agent is the right tool are: messy unstructured
input that needs structuring, repetitive cognitive work that humans
hate, and decisions where the LLM proposes and a human disposes. This
project hits all three. Most B2B operations use cases I see don't."

**Killer detail:** "The dumb test I run: would I rather pay a human one
hour to do this once, or pay an engineer two weeks to ship an agent that
does it 1,000 times? If the answer is 'one hour,' don't build the agent."

---

## Closing notes

Read this twice. Then close it and rewrite both halves in your own words.
The interviewer can't tell what you've read; they can absolutely tell
when you're reciting. The goal is for these answers to feel like *yours*,
informed by what you actually built and shipped.

Three things you should have memorized cold:
1. The 30-second pitch.
2. The architecture diagram (be able to draw it on a whiteboard).
3. The T-10003 self-correction trace, step by step.

Everything else is reasoning under pressure, which is what they're
actually testing.

Good luck.
