# Branch-basic implementation and verification

The backend gate passed before frontend development; see
[the measured gate evidence](backend-readiness.md). Separate FastAPI and Next.js
applications now implement the combined founder workflow through HTTP and SSE.
The [branch integration notes](branch-basic-integration.md) identify which features
come from main and branch-one, including the main snapshot's lack of a separate
conversational chatbot.

Implemented: manual startup inputs and optional matching, explicit lead/profile
confirmation, exact funding-event co-investor joins, bounded profiling, deterministic
scores and completeness, sourced considerations, founder exclusions, provisional
lead wording, introduction/follow-up drafts, individual and combined copy actions,
do-not-contact reruns, partial/no-match/no-allocation/error states, progress replay,
real-response caching, budgets, and configurable model providers.

Validation completed locally:

- 162 backend tests passed, including main scoring parity, allocation arithmetic,
  auto-detected leads, metadata provenance, resolver fallback, lead-alias
  preservation and sourced result-chat answers.
- On the earlier branch-one snapshot, ten real Cala API runs across three hypothetical founder profiles passed budget,
  timing, source, shared-event, draft and SSE assertions.
- A supplemental complete API run produced five validated Codex assessments in
  17.938 seconds with cached real Cala responses.
- Strict frontend TypeScript and an optimized Next.js production build passed.
- Ten pure graph regression checks and eight browser tests passed. Browser checks
  cover the responsive Cala testing profile, no allocation with zero Cala calls,
  and a real Seedcamp flow with progress, graph selection/zoom, sourced chat,
  copied provisional follow-up text, a do-not-contact rerun, full metadata with
  dated field sources, selectable matches when metadata is unavailable, the main
  form/detected lead, main scoring flags, allocation percentages, and the insights
  and activity tabs.
- Desktop/mobile screenshots were inspected; text size, contrast, labels and
  responsive layout were corrected based on those checks.

Current limits: native graph coverage is bounded to three observed funding events
and is explicitly partial; investor details remain unknown when not retrieved.
Drafts use sourced templates. The model supplies labelled assessments and never
numeric scores. Model timeout falls back safely. Founder feedback on an actionable
request is still outstanding. No public deployment, accounts or automatic outreach
are included. The requested estimated allocation feature is included; broader
founder-network exploration is still outside this branch's discovery scope.

The results page now follows the three-panel workspace: main's agent insights upper left,
up to five compact investor entries below it, and an interactive knowledge graph
on the right. Selection is shared across the graph, list and conversation. Full
evidence, scoring and drafts open in a keyboard-accessible details dialog. Solid
edges use identified Cala funding events; the current founder-to-lead route is
dashed and labelled founder-supplied. Mobile screens stack the same panels.
The upper-left panel follows the user's clarified main-branch choice: Current
understanding explanations and the live agent activity trace. The local result
conversation API remains available for compatibility, with answers scoped to
the saved result.

Native entity resolution now enriches basic search matches with all properties
advertised by Cala's entity schema and available geography/category relationships.
The same parser supplies full investor-profile metadata. Headquarters country,
registration address, legal entity type and investor category remain distinct;
unknown or conflicting facts stay unknown. Field-specific sources support
geography and category filters. Optional enrichment shares the resolver budget
and preserves basic matches when details fail or time out. Confirmation and
candidate details expose the returned properties and their dated sources.
The live default Cala lookup verified United States / VC for the main Lightspeed
entity and Mauritius / VC for the two Mauritius vehicles, plus desktop/mobile
layout and the normalized website link.

The combined default Cala API flow detected Lightspeed from an exact company
funding event and returned five cards: four shortlisted and one flagged under
main's rules. One measured warm-data run completed in 19.25 seconds with 19 raw
cache hits and zero new processing calls. All cards had saved introduction
requests. Allocation correctly retained the full EUR 2 million as still open
because no eligible EUR ticket estimate was available. This is a single measured
integration run, not a new latency benchmark.

Credentials live only in the ignored backend `.env`. GitHub users configure their
own Cala key and either local Codex login or an OpenAI-compatible model endpoint,
key and model. See [backend setup](../backend/README.md) and
[frontend setup](../frontend/README.md).
