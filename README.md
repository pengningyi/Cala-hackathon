# RoundCloser

Find co-investors who participated in an identified funding round with your lead,
using Cala evidence and a copyable introduction request.

The [MVP PRD](<Co-Investor Finder — MVP PRD.md>) is the project plan. Development
starts with an independently runnable Python/FastAPI backend. The Next.js frontend
consumes the backend's HTTP/SSE API after its live Cala readiness gate.

Implemented workflow:

- Manual founder/startup profiles, EUR validation, lead resolution and explicit confirmation.
- Same-round graph discovery, bounded parallel profiles, founder exclusions and advisory considerations.
- Fixed PRD scoring, separate completeness, source metadata, introduction/follow-up templates.
- A 90-second run budget, retry/call limits, real-response caching and replayable SSE progress.
- Configurable semantic assessment through a locally logged-in Codex CLI or your own OpenAI-compatible model API.
- A responsive frontend with confirmation, live progress, sourced cards, copy actions and do-not-contact reruns.

The native Cala integration is verified against live funding-event relationships.
Ten confirmed runs across three hypothetical founder profiles passed the backend
acceptance checks. Discovery is deliberately bounded, so its results explicitly
report partial coverage. There is no runtime demo-data substitute.

Supply your own `CALA_API_KEY` in the ignored `backend/.env`. Model assessment
defaults to your locally logged-in Codex CLI; an OpenAI-compatible API endpoint,
key and model can be configured instead. All credentials stay in the backend.

Run the [backend](backend/README.md) and [frontend](frontend/README.md) separately.
The frontend opens at `http://localhost:3000`, and backend API documentation at
`http://127.0.0.1:8000/docs`.

See the [HTTP contract](docs/api.md), [backend readiness evidence](docs/backend-readiness.md),
and [implementation status](docs/implementation-status.md).
