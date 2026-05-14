# Dispatcher Dashboard (placeholder)

A Next.js + React app that connects to the FastAPI backend and renders:

- **Ticket inbox** (live from `GET /tickets`)
- **Triage view** that streams the agent's per-node updates from
  `POST /tickets/triage/stream` (SSE) — the user watches the agent reason in real time.
- **Plan card** with one-click "Approve & dispatch" or "Send back for review"
  (the human-in-the-loop step).
- **LangSmith link** beside every plan so the interviewer can click straight into
  the trace.

## Suggested scaffold (to add later)

```bash
npx create-next-app@latest dispatcher-dashboard --typescript --tailwind --app
cd dispatcher-dashboard
npm install lucide-react @radix-ui/react-dialog
```

Then point `NEXT_PUBLIC_API_BASE` at `http://localhost:8000`.

For an interview, this can stay a stub — the value is the streaming backend +
the LangSmith trace, both already wired in `src/enpal_dispatcher/api.py`.
