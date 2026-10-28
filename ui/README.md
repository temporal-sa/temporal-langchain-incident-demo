# ui/: the console

A React app (Vite, React Flow) that starts runs, sends approvals and questions, and draws what's happening. Everything it shows comes from Temporal's event history, through the console API in `python/`.

```bash
npm install
npm run dev             # http://localhost:5173; proxies /api to 127.0.0.1:8000
```

Set `UI_PORT` to use another port, and `API_URL` if the API isn't on 127.0.0.1:8000. To host a built console apart from the API, see *Serve the console separately from the API* in `docs/extending.md` (`VITE_API_BASE` plus the API's `CORS_ORIGINS`).

## Where things are

| Path | What's there |
|---|---|
| `src/components/FlowGraph.jsx` | The architecture graph: People, Temporal (workflows and activities), and standard services |
| `src/components/SequenceView.jsx` | The step-by-step sequence diagram, with `loop`, `par`, and `tool` frames |
| `src/components/Conversation.jsx` | The session panel and follow-up questions |
| `src/components/ApprovalCard.jsx` | The human-in-the-loop decision, sent as a Temporal Update |
| `src/useFollow.js` | Keeps panels on their newest entry without ever scrolling the page |
| `src/lanes.js` | Colors and labels shared by both views |
