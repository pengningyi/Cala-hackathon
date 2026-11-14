# Branch-basic implementation and verification

The backend gate passed before frontend development; see
[the measured gate evidence](backend-readiness.md). Separate FastAPI and Next.js
applications now implement the combined founder workflow through HTTP and SSE.
The [branch integration notes](branch-basic-integration.md) identify which features
come from main and branch-one. The result conversation adds live generation
through the configured Codex CLI or model API.

Implemented: identity-first company/lead matching, explicit pair confirmation,
sourced prefills with empty unknowns and founder-reviewed round details, exact funding-event co-investor joins, bounded profiling, deterministic
scores and completeness, sourced considerations, founder exclusions, provisional
lead wording, introduction/follow-up drafts, individual and combined copy actions,
do-not-contact reruns, partial/no-match/no-allocation/error states, progress replay,
real-response caching, budgets, and configurable model providers.

Validation completed locally:

- 205 backend tests passed, including identity-first matching/confirmation and
  sourced industry prefills, main scoring parity, allocation arithmetic,
  auto-detected leads, metadata provenance, resolver fallback, lead-alias
  preservation, offline compatibility helpers and 22 live-chat cases covering
  model transports, history, evidence validation, capacity and cancellation.
- On the earlier branch-one snapshot, ten real Cala API runs across three hypothetical founder profiles passed budget,
  timing, source, shared-event, draft and SSE assertions.
- A supplemental complete API run produced five validated Codex assessments in
  17.938 seconds with cached real Cala responses.
- Strict frontend TypeScript and an optimized Next.js production build passed.
- The initial integration passed ten pure graph checks and eight browser tests. Those checks
  cover the responsive Cala testing profile, no allocation with zero Cala calls,
  and a real Seedcamp flow with progress, graph selection/zoom, sourced chat,
  copied provisional follow-up text, a do-not-contact rerun, full metadata with
  dated field sources, selectable matches when metadata is unavailable, the main
  form/detected lead, main scoring flags, allocation percentages, and the insights
  and activity tabs.
- All 22 current frontend checks passed: ten graph checks and twelve browser
  checks for identity confirmation, empty/editable unknowns, changing companies,
  retry, metadata, allocation, live chat and the real Cala search/rerun flow.
  Desktop, tablet and mobile layout checks passed.
- Live Cala AI/Lightspeed matching filled the supported website, industry and HQ
  fields after both identities were confirmed. Stage, target and committed amount
  stayed empty, and matching/confirmation triggered no funding-discovery request.
  Normalized website matching preserved all 11 website-specific source records.
- A real Cala search followed by two Codex chat turns passed in the browser.
  Model generation took 8.7 and 6.0 seconds, returned validated source references,
  preserved follow-up history and the immutable result, and produced no browser
  errors. Conversation persisted across the activity tab; mobile layout passed.
- Desktop/mobile screenshots were inspected; text size, contrast, labels and
  responsive layout were corrected based on those checks.

The [connection-strategy review](connection-improvement-plan.md#implementation-review-14-november-2026)
confirms that pagination, stage-prioritized event discovery and founder/board
network expansion are still missing. Current exact-event validation, bounded
retrieval, provenance and evidence chat are baseline safeguards; they do not
satisfy the proposed discovery acceptance gate.

Current limits: native graph coverage is bounded to three observed funding events
and is explicitly partial; investor details remain unknown when not retrieved.
Drafts use sourced templates. The model supplies labelled assessments and never
numeric scores. Assessment timeout leaves semantic fields unknown; live chat
returns an explicit retryable error on model failure. Founder feedback on an actionable
request is still outstanding. No public deployment, accounts or automatic outreach
are included. The requested estimated allocation feature is included; broader
founder-network exploration is still outside this branch's discovery scope.

The results page now follows the three-panel workspace: up to five compact investor entries upper left,
with estimated amounts and allocation percentages in each investor row, the Still
open remainder and an expandable estimate basis replacing the separate allocation
card. A live model chatbot sits below them, with an interactive knowledge graph
on the right. Selection is shared across the graph, list and conversation. Full
evidence, scoring and drafts open in a keyboard-accessible details dialog. Solid
edges use identified Cala funding events; the current founder-to-lead route is
dashed and labelled founder-supplied. Mobile screens stack the same panels.
The lower-left panel offers Chat and Agent activity tabs. Each user question
invokes the configured model with recent history, selected investor and saved
run evidence. Successful replies are marked as model responses; citations and
investor references are validated server-side. Generation errors retain the
question for retry. Chat does not change rankings or perform new discovery.

Setup now asks only for company and lead names, then confirms both Cala IDs.
The editable round form appears afterward with supported company prefills and
blank current-stage/target/commitment fields. The previous separate Connections
navigation step is merged into Your round. Changing companies clears old values;
expired confirmation requires another lookup. The legacy full-profile API remains
available to existing clients.

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

An earlier full-profile API check detected Lightspeed from an exact company
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
