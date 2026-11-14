# RoundCloser — branch-basic

Find co-investors who participated in an identified funding round with your lead,
using Cala evidence and a copyable introduction request.

This branch combines identity-first setup, main's scoring and allocation
estimates with branch-one's interface, knowledge graph, full entity
metadata and copyable introduction requests. See the
[integration notes](docs/branch-basic-integration.md) for feature provenance and
the precise scoring/estimate rules.

The lower-left panel contains a live model chatbot and the agent activity trace.
Chat uses your configured Codex CLI or OpenAI-compatible API, with conversation
history and the current run's sourced investor evidence.

The [MVP PRD](<Co-Investor Finder — MVP PRD.md>) is the project plan. Development
starts with an independently runnable Python/FastAPI backend. The Next.js frontend
consumes the backend's HTTP/SSE API after its live Cala readiness gate.

Implemented workflow:

- Company and lead names first, explicit confirmation of both Cala matches, sourced prefills, then founder-completed round details.
- Same-round graph discovery, bounded parallel profiles, founder exclusions and advisory considerations.
- One rule set for scoring and ranking: 12 plain rules (Connection, Activity, Fit, Risk), one point per rule met, shown as "N rules met"; source metadata and introduction/follow-up templates.
- Trust loops from public professional roles (founder, lead partner, common contacts), a likelihood x trust action, and top-five set selection that aims to fill 1.5-2x the open amount.
- Main's greedy allocation percentages, labelled as estimates from historical EUR round totals and observed investor counts.
- Candidates from the lead's shared rounds, your company's existing investors and backers of the founders' previous companies (no shared lead round required).
- A 120-second, 72-call run budget, retry limits, real-response caching and replayable SSE progress.
- Live result chat and semantic assessment through a locally logged-in Codex CLI or your own OpenAI-compatible model API.
- A responsive frontend with confirmation, live progress, sourced cards, copy actions and do-not-contact reruns.

Discovery uses verified Cala funding-event relationships and is deliberately
bounded, so results explicitly report partial coverage. Historical investment
does not establish a commitment to the current round. Unknown ticket estimates
remain unallocated. There is no runtime demo-data substitute.

Supply your own `CALA_API_KEY` in the ignored `backend/.env`. Model generation
defaults to your locally logged-in Codex CLI; an OpenAI-compatible API endpoint,
key and model can be configured instead. All credentials stay in the backend.

Run the [backend](backend/README.md) and [frontend](frontend/README.md) separately.
The frontend opens at `http://localhost:3000`, and backend API documentation at
`http://127.0.0.1:8000/docs`.

See the [HTTP contract](docs/api.md), [backend readiness evidence](docs/backend-readiness.md),
and [implementation status](docs/implementation-status.md).
