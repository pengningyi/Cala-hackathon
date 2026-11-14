# Implementation and verification

The backend gate passed before frontend development; see
[the measured gate evidence](backend-readiness.md). Separate FastAPI and Next.js
applications now implement the P0 founder workflow through HTTP and SSE.

Implemented: manual startup inputs and optional matching, explicit lead/profile
confirmation, exact funding-event co-investor joins, bounded profiling, deterministic
scores and completeness, sourced considerations, founder exclusions, provisional
lead wording, introduction/follow-up drafts, individual and combined copy actions,
do-not-contact reruns, partial/no-match/no-allocation/error states, progress replay,
real-response caching, budgets, and configurable model providers.

Validation completed locally:

- 82 backend tests passed, including lead-alias preservation and sourced result-chat answers.
- Ten real Cala API runs across three hypothetical founder profiles passed budget,
  timing, source, shared-event, draft and SSE assertions.
- A supplemental complete API run produced five validated Codex assessments in
  17.938 seconds with cached real Cala responses.
- Strict frontend TypeScript and an optimized Next.js production build passed.
- Ten pure graph regression checks and three browser tests passed. Browser checks
  cover the responsive Cala testing profile, no allocation with zero Cala calls,
  and a real Seedcamp flow with progress, graph selection/zoom, sourced chat,
  copied provisional follow-up text and a do-not-contact rerun.
- Desktop/mobile screenshots were inspected; text size, contrast, labels and
  responsive layout were corrected based on those checks.

Current limits: native graph coverage is bounded to three observed funding events
and is explicitly partial; investor details remain unknown when not retrieved.
Drafts use sourced templates. The model supplies labelled assessments and never
numeric scores. Model timeout falls back safely. Founder feedback on an actionable
request is still outstanding. No public deployment, accounts, automatic outreach
or P1 features are included.

The results page now follows the three-panel workspace: conversation upper left,
up to five compact investor entries below it, and an interactive knowledge graph
on the right. Selection is shared across the graph, list and conversation. Full
evidence, scoring and drafts open in a keyboard-accessible details dialog. Solid
edges use identified Cala funding events; the current founder-to-lead route is
dashed and labelled founder-supplied. Mobile screens stack the same panels.
The current chat is a local evidence assistant for saved results, rather than an
open-ended model conversation; it can explain, compare and show existing drafts.

Credentials live only in the ignored backend `.env`. GitHub users configure their
own Cala key and either local Codex login or an OpenAI-compatible model endpoint,
key and model. See [backend setup](../backend/README.md) and
[frontend setup](../frontend/README.md).
