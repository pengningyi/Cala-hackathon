# RoundCloser — branch-basic

Find co-investors who participated in an identified funding round with your lead,
using Cala evidence and a copyable introduction request.

This branch combines main's founder form, lead auto-detection, scoring and
allocation estimates with branch-one's interface, knowledge graph, full entity
metadata and copyable introduction requests. See the
[integration notes](docs/branch-basic-integration.md) for feature provenance and
the precise scoring/estimate rules.

Main's current source has a live agent trace and a “Current understanding” panel,
rather than a conversational chat implementation. These now occupy the upper-left
agent-insights panel, with Current understanding and Agent activity tabs.

The [MVP PRD](<Co-Investor Finder — MVP PRD.md>) is the project plan. Development
starts with an independently runnable Python/FastAPI backend. The Next.js frontend
consumes the backend's HTTP/SSE API after its live Cala readiness gate.

Implemented workflow:

- Main's basic founder form, four stages, EUR shorthand inputs, optional lead detection and explicit confirmation.
- Same-round graph discovery, bounded parallel profiles, founder exclusions and advisory considerations.
- Main's deterministic scoring and review flags, separate completeness, source metadata and introduction/follow-up templates.
- Main's greedy allocation percentages, labelled as estimates from historical EUR round totals and observed investor counts.
- A 90-second run budget, retry/call limits, real-response caching and replayable SSE progress.
- Configurable semantic assessment through a locally logged-in Codex CLI or your own OpenAI-compatible model API.
- A responsive frontend with confirmation, live progress, sourced cards, copy actions and do-not-contact reruns.

Discovery uses verified Cala funding-event relationships and is deliberately
bounded, so results explicitly report partial coverage. Historical investment
does not establish a commitment to the current round. Unknown ticket estimates
remain unallocated. There is no runtime demo-data substitute.

Supply your own `CALA_API_KEY` in the ignored `backend/.env`. Model assessment
defaults to your locally logged-in Codex CLI; an OpenAI-compatible API endpoint,
key and model can be configured instead. All credentials stay in the backend.

Run the [backend](backend/README.md) and [frontend](frontend/README.md) separately.
The frontend opens at `http://localhost:3000`, and backend API documentation at
`http://127.0.0.1:8000/docs`.

See the [HTTP contract](docs/api.md), [backend readiness evidence](docs/backend-readiness.md),
and [implementation status](docs/implementation-status.md).
