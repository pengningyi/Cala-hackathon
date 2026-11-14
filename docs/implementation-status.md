# Branch-basic implementation and verification

## Canvas knowledge graph explorer — 14 November 2026

The `force-knowledge-graph` skill now guides the results diagram. A client-only
`react-force-graph-2d` renderer replaces the SVG physics view. Organizations remain
circles and people squares, sized by unique neighbors. Funding events remain
individually sourced edges to their actual recipient, with separate curved lines
for parallel evidence. No scoring, allocations or full-metadata retrieval changed.

The explorer adds entity search, entity and relationship whitelists, directions
relative to explored hubs, an accessible entity browser, pointer context menus,
expanded view and PNG export. Zoom reveals markers, icons, short labels and full
cards. Selected names stay readable in the overview. Explicit focus takes priority
over the initial settled fit, and position memory survives filtering and expansion.

Expansion still uses only the retrieved, bounded run snapshot; it does not invent
relationships or make additional Cala/model calls. The inspector retains event
dates, role validity, field metadata and source publication/retrieval dates. The
founder-selected lead route stays separate from sourced public evidence.
Reduced motion disables particles, animated camera transitions and animated settles.

Verification includes nine new adapter/filter/curvature/position tests and six
mocked browser cases covering evidence, filters, expansion, zoom, pointer and
keyboard actions, reduced motion, PNG export and expanded view. A saved Cala
replay renders 27 entities and 56 connections without browser errors or horizontal
overflow at 1440, 768 and 390 pixels. Production build and typecheck pass.

## Physical graph exploration — 14 November 2026

Nodes now use springs, neighbor repulsion and collisions that account for their
current visual size. Dragging pins a body temporarily; release returns it toward
its hub. Expansion promotes a node to a hub and reveals up to eight additional
neighbors from the saved, sourced result. Multiple hubs can remain expanded;
collapse preserves neighbors still required by another hub or the initial view.
Expanded views are bounded to 72 nodes and 60 people, without new API/model calls.

Zoom progressively shows points, entity icons and named detail cards. Wheel,
trackpad/touch pinch, buttons and keyboard navigation retain the selected context.
The simulation stops when settled or offscreen, pauses when the tab is hidden,
and settles without animated bursts when reduced motion is requested.

The production build and all 56 selected frontend checks passed, including 14
physics cases, 31 evidence/expansion cases and 11 browser workflow cases. A saved
Cala replay expanded from 27 to 35 nodes and verified all three detail levels,
wheel zoom, mobile selection and an error-free browser console.

## Entity-based knowledge graph — 14 November 2026

The graph now represents people as squares and organizations as circles, without
the former column headings or funding-event nodes. Each funding edge connects
an identified investor to the actual recipient company and retains the event,
date and source evidence. Sourced person roles connect founders, investor staff
and common contacts; companies in founder-network paths remain organizations.

Any node or connection can be selected and toggled, and nodes can be dragged.
Node size measures distinct neighbors in the displayed, bounded view, rather
than ranking score or repeated references to the same entity. A visible notice
identifies truncated views. Missing people data does not create inferred people.

The backend's optional `people` result field preserves roles already retrieved
for discovery and scoring, including source links, metadata and validity dates.
This adds no Cala requests and does not change ranking, scores or allocations.

Verification: 293 backend tests, 25 graph-model checks, two graph/workspace browser
checks and the production build passed. A replay of saved Cala evidence verified
mouse toggling and mobile access for all 27 displayed entities, clickable funding
and affiliation sources, and a layout without overlapping label boxes.

## Current owner merge — 14 November 2026

Current source baseline: owner `branch-basic` at `df78081`, including the owner's
12-rule scoring, rule-count UI, founder-network candidates, trust loops and action
recommendations. The policy files remain unchanged. Local integration restores
full Cala metadata enrichment after REST matching, address-country fallbacks,
field-specific sources and an HQ-only data projection for portfolio geography.

Verification of this integration:

- All 288 backend tests passed, including the owner's ranking, rule scoring,
  trust loops, founder-network workflow and the full-metadata merge regressions.
- All 27 selected frontend checks passed: owner score explanations and action
  ladder, graph invariants, setup/chat behavior and nine metadata cases.
- TypeScript and the optimized Next.js production build passed.
- A real Cala AI/Kfund lookup returned 17 startup properties and nine lead
  properties through full metadata reads. The confirmed startup retained its
  headquarters evidence; the lead retained its registration-address fallback.
- The real confirmed workflow completed in 51.953 seconds, used 61 of the owner's
  72-call budget and returned five cards under `rules-2026-11`, with scores
  10, 9, 8, 4 and 4. Three cards carried trust loops and existing/founder-network
  origins. Graph coverage remains explicitly partial.
- Live Codex chat over the real result passed with 16 validated citations after
  constraining structured source/candidate IDs to the saved catalog and shortlist.
  The owner's DeepSeek JSON-object compatibility fallback remains unchanged.
- Final metadata browser checks confirm that office/registration fallbacks stay
  visible and sourced while unverified founder headquarters remains empty/editable.
  Desktop/mobile visual review confirmed the owner's layouts, rule explanations,
  trust loops and footer; no horizontal overflow or browser errors were observed.

The remainder of this document records earlier snapshots and should not be read
as the current owner's scoring or discovery policy.

## Earlier implementation and verification

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
