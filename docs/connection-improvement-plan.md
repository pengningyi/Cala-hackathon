# RoundMe: connection improvement plan

**Date:** 14 November 2026

**Status:** Reviewed against the local `branch-basic` implementation on 14 November 2026. Core Phase 1 discovery changes and Phase 2 network expansion remain unimplemented; existing safeguards are listed below.

**Basis:** [MVP PRD](../Co-Investor%20Finder%20%E2%80%94%20MVP%20PRD.md) and
[live Cala API audit](cala-connection-audit.md).

## Implementation review: 14 November 2026

The company-matching, metadata, allocation and live-chat changes do not implement
the proposed connection-discovery strategies. The current main shortlist still
comes from a bounded sample of shared funding events.

| Strategy or safeguard | Current status | Evidence |
| --- | --- | --- |
| Paginated funding discovery | Missing | Native projections request `limit: 20` per relationship without an `offset` or paging loop; see [native projection](../backend/app/cala_native.py#L338). |
| Seed, Series A and unknown discovery tiers | Missing | [Lead graph discovery](../backend/app/cala_native.py#L257) accepts lead ID/lookback, sorts the sampled events by available date/ID, and expands up to three events. Stage matching happens after expansion, not before event retrieval. |
| Per-event selection reasons and new discovery tests | Missing | No second-page-only event, repeated-page stop, stage-tier or selection-reason test is present. Existing graph/budget tests cover the current sampling policy. |
| Funding and corporate-event relationships | Existing baseline | Both relationship types and event-ID deduplication within the retrieved sample are implemented in [native discovery](../backend/app/cala_native.py#L261). |
| Exact event joins, dated evidence, conflicts and exclusions | Existing baseline | [Scoring safeguards](../backend/app/scoring.py#L165) and [workflow qualification](../backend/app/pipeline.py#L465) reject unsupported joins, conflicting events and unusable dates. |
| Call/time limits, caching and partial results | Existing baseline | [Runtime budgets](../backend/app/runtime.py#L81) retain the 20-call/four-concurrent/90-second limits and assembly reserve. Native discovery explicitly reports partial coverage. |
| Founder, previous-company, board and fund paths | Missing | There is no broader path model, relationship traversal or Explore founder network API/action. The existing founder node represents the startup and its founder-selected lead route, not a retrieved person/board network. |
| Separate network budget, progress and per-hop role dates | Missing | [GraphSnapshot](../backend/app/models.py#L265) contains funding rounds only; there is no independent founder-network result. |
| Shared selection, evidence chat and founder-supplied route | Existing baseline | Shortlist, graph and live chat share selection. Chat explains the saved result and does not discover missing paths. The dashed route is attributed to the founder. |
| Introduction-access status | Partial baseline | Current commitment/provisional labels and cautionary wording exist, but there is no independent typed access status or dated founder access report. |

The Exa second-page discovery and Cala ? founder ? Vilynx ? board ? fund examples
remain proposals. Phase 1 is not ready for its acceptance gate, and Phase 2 has
not started. The next implementation priority is paginated, stage-aware discovery;
founder/board exploration remains a separate later scope.

Later user requests also qualify two older acceptance criteria: the identity-first
browser performs company lookups before round amounts are entered, so a filled
round guarantees zero **funding-discovery** calls rather than zero company-lookup
calls. Manual startup profiles remain available through the legacy full-profile
API; the current browser requires confirming both company identities. Scoring
uses the explicitly selected main-branch rules and version, including zero for
unknown inputs; the numerical weights remain 35/20/15/15/10/5.

## Update: scoring v2 and trust loops (14 November 2026)

Founder, previous-company and board paths are now computed inside the run, not
as a separate on-demand action, using a raised budget (48 calls, 120 seconds).
See `backend/app/trust.py`. Loops remain labelled unconfirmed public overlaps
and only affect `trust` and the recommended action, never `fit_score`. The
frozen 35/20/15/15/10/5 weights were replaced by scoring v2 (`backend/app/ranker.py`);
paginated, stage-aware discovery is still not implemented.

## 1. Intended outcome

Help a founder answer three questions for each potential investor:

1. Why might this investor fit our current round?
2. What evidence connects them to our lead or founder network?
3. Who should we ask, and what still needs confirmation?

The first release improves discovery of co-investors who participated with the
confirmed lead in the same identified funding event. A later extension adds
founder, previous-company and board paths with their own evidence labels.

A shared investment or organizational affiliation does not establish personal
closeness, a willingness to introduce, current investment appetite or a commitment
to the founder's round.

## 2. Problem in the current implementation

The native provider retrieves a limited first page of funding relationships,
sorts the returned sample by available event dates, and expands three events.
Those events are not guaranteed to be the lead's latest or most relevant rounds.
Many projected funding edges have no event date at all.

This can produce a shortlist dominated by large late-stage syndicates even when
the founder is raising a seed round. Changing the final score alone cannot
recover relevant investors whose funding events were never explored.

The current graph also omits useful Cala relationships involving founders,
previous companies, board members and fund affiliations.

## 3. Two examples using the Cala test profile

The form's seed round, amounts and current lead commitment are hypothetical test
inputs. Public historical investment data must remain distinct from those inputs.

### Example A: find a relevant shared funding event

"Paginated" means reading records in batches: the first 20, the next 20, and so
on within a defined budget. "Stage-aware" means examining observed seed and
relevant early-stage events before spending the event-expansion budget on other
events. It does not imply chronological API ordering.

The live audit found Exa's Series A on the second page of Lightspeed's funding
relationships:

```text
Lightspeed Venture Partners -> Exa Series A <- NVentures
```

Retrieving that exact event identified Exa as the issuer and both investors as
participants. Y Combinator was another participant in the same event.

An illustrative result would say:

| Field | Display |
| --- | --- |
| Investor | NVentures |
| Connection evidence | Verified shared-round connection: Exa Series A |
| Route from Cala | Through the founder-selected Lightspeed lead; provisional in this test |
| Stage evidence | Historical Series A participation; current seed appetite unconfirmed |
| Next action | Ask the lead whether an introduction is available and whether the investor considers this seed opportunity |

This improves discovery. Investor suitability still requires its own activity,
stage, sector, geography, participation-size and portfolio-overlap assessment.

### Example B: explore a founder and board path

Cala returned the following typed relationships:

```text
Cala AI <- founded by - Elisenda Bou-Balust
Elisenda Bou-Balust - founded -> Vilynx
Vilynx <- board member - Sonia Fernandez
Sonia Fernandez - observed employment affiliation -> Kibo Ventures
```

Display this as **Historical founder/board path**, with
**Introduction access unconfirmed**. Keep the old board date and the employment
edge's available sources visible. Do not infer a current title from a missing end
date or assume that the people know each other personally.

Kibo already appears as a Cala backer in the retrieved data. This path adds named
relationship context; it does not make Kibo a newly discovered prospect. Other
Vilynx backers, such as Caixa Capital Risc and Ridgewood Capital, are historical
founder-network leads whose present suitability also needs investigation.

## 4. Phase 1: improve lead-connected funding discovery

This phase improves the existing P0 workflow and retains its shortlist rules.

### Retrieval sequence

1. Use the founder-confirmed lead ID. Inspect its schema with
   `entity_introspection`; reuse fresh cached schema responses where available.
2. Retrieve advertised funding relationships with explicit projections. Support
   both `PARTICIPATES_IN_FUNDING_ROUND` and funding-event instances reached through
   `PARTICIPATES_IN_CORPORATE_EVENT`.
3. Read additional relationship pages using `limit` and `offset`. Deduplicate by
   stable event ID across pages and relationship types. Stop at the configured
   page/call/time cap, an empty or short page, or a repeated page with no new IDs.
   A stopping condition does not by itself establish complete graph coverage.
4. Prioritize events for exact retrieval using available stage labels and sourced
   company context. For the seed test, use explicit discovery tiers: known seed
   events first, then relevant Series A events, with a bounded allowance for
   unknown or other stages. Freeze the policy before scenario testing and record
   the selection reason in the trace. Missing context stays unknown.
5. Introspect and retrieve the selected exact events. Validate their identity,
   issuer, dates, investor roles and source links before creating candidate paths.
6. Apply founder exclusions and preferences, select at most 10 preliminary
   investors, profile at most five, and assemble the existing cards and drafts.

Stage priority changes which records we investigate. A Series A event does not
count as an exact seed match in final scoring. Historical stage mismatch remains
advisory; only an explicit founder filter or an applicable published mandate can
exclude an investor on that basis.

### Evidence requirements

Every main-shortlist connection must contain the same identified event ID for the
selected lead and candidate, an identified issuer, a usable event date within the
configured lookback, and sources for both investor participations. Company-level
`INVESTED_IN` overlap alone does not satisfy this rule.

Keep event dates, announcement dates, source publication dates and retrieval
timestamps distinct. Never substitute a source date for a missing event date.
Do not merge different event IDs merely because company names and stages match.
Handle conflicting identities or observations explicitly.

### Budget

Retain the PRD defaults: 20 actual Cala data calls, four concurrent requests,
90 seconds after confirmation, and the final 10 seconds reserved for assembly.
Retries and introspection calls consume the same data-call budget. Cache hits do
not consume a new network data call; their timestamps remain visible. Protocol
initialization and tool discovery still consume wall-clock time.

An illustrative cold-cache allocation is:

| Work | Maximum calls in this example |
| --- | ---: |
| Lead introspection and first relationship projection | 2 |
| One additional relationship page | 1 |
| Introspection and retrieval for three exact events | 6 |
| Introspection and retrieval for five investor profiles | 10 |
| Remaining headroom for an optional company read or a retry | 1 |
| Total | 20 |

This is a planning allocation, not a promise of five results or a latency
guarantee. Reduce work when time or calls run short, retain the assembly reserve,
and return available sourced cards with partial-coverage notices. Additional
pages must trade against other retrieval work rather than silently exceed caps.

Broad `knowledge_query` and `knowledge_search` calls are optional discovery
helpers. The audit observed approximately 50 and 38 seconds respectively for one
call of each. Do not make either an unconditional prerequisite. Validate returned
IDs and exact events before promoting query suggestions into the shortlist.

## 5. Phase 2: add founder and board paths

This is a scope extension beyond the P0 same-round shortlist, related to the
PRD's P1 partner-level introduction paths. It should ship after Phase 1 is
validated.

Use a separate, on-demand **Explore founder network** action with its own visible
request/time limits and partial-result state. These extra hops cannot be assumed
to fit alongside five cold profiles in the existing 20-call run.

Start from a founder-confirmed Cala startup entity. Retrieve advertised incoming
`FOUNDED` relationships, then selectively expand previous companies, historical
investors, board memberships and fund affiliations. Use explicit per-page,
per-depth and overall node limits; cap a displayed path at four relationships
from the startup. Avoid cycles and stop when the budget is exhausted.

Useful relationship types, when advertised for the entity, include:

| Relationship | Purpose |
| --- | --- |
| `FOUNDED` | Connect a founder to their current or previous company |
| `INVESTED_IN` | Identify historical company backers |
| `IS_BOARD_MEMBER_OF` | Identify an observed board affiliation |
| `WORKS_AT` | Connect a named person to a fund or company |
| `IS_MEMBER_OF` | Retain an explicit partner, advisor or board role where supplied |

Manual startup profiles must continue to work without this optional expansion.
If there is no confirmed startup match, explain that public founder-network
exploration requires identity confirmation; do not guess the company or founders.

Keep broader paths outside the main top-five co-investor list unless the investor
independently satisfies the exact shared-round rule. Apply do-not-contact,
declined, committed and investor-type rules consistently to any suggested action.
Existing shareholders remain eligible for follow-on under the original rules;
past backing alone never sets current-round commitment.

## 6. Evidence, fit and presentation

Preserve the requested results layout: the shortlist above the insights panel on the
left, with the knowledge graph on the right. Add the new information within those
panels and the existing details view.

Use plain connection labels:

| Label | Meaning |
| --- | --- |
| Verified shared-round connection | Cala identifies both investors in the same funding event |
| Historical founder/board path | Sourced founder, prior-company, board or affiliation hops connect the entities |
| Founder-reported introduction access | The founder explicitly reports an available route; attribute the statement and date |
| Introduction access unconfirmed | There is no confirmation that the route can produce an introduction |

Connection type and introduction access are separate fields: a verified shared
round can still have unconfirmed access. Preserve the dashed, founder-supplied
current-lead edge and provisional wording for conditional commitments.

Show investment fit beside connection evidence. Keep the PRD's frozen score
weights and separate profile completeness. This proposal does not introduce a
numeric personal-relationship score or treat an unknown field as a verified fit.
If scoring weights are reconsidered later, version and evaluate that change
separately.

Every stored path should retain endpoint IDs, relationship direction and type,
role, available validity dates, per-hop source links and source dates, and actual
retrieval timestamps. Founder reports require separate attribution. Resolve
identity by confirmed IDs or explicit organizational relationships; names alone
must not merge companies, fund vehicles or people.

Investor selection should continue to synchronize across the shortlist, graph
and chat. Chat may explain only the evidence present in the relevant stored
snapshot. Drafts should ask the lead to confirm access and suitability rather than
assert an unverified personal relationship. Do not add automatic outreach,
contact enrichment, accounts or CRM functionality.

## 7. Acceptance criteria

Phase 1 is ready when:

- A relevant event available only on the second page can enter discovery and
  produce a candidate after exact-event validation.
- Same-company investments in different rounds cannot create a shared-round
  connection. Duplicate pages and source references do not inflate counts.
- Missing edge dates do not become source dates or fabricated event dates.
- Discovery distinguishes seed, Series A and unknown observations without
  changing final stage normalization or frozen scoring weights.
- Conflicting identities and missing or inconsistent query IDs cannot create
  unsupported joins.
- Every returned candidate passes existing exclusion/status rules and has a
  copyable introduction or follow-up request.
- Cold and warm runs report actual calls, elapsed time, cache use, retrieval
  scope and partial coverage. Limits and assembly reserve are respected.
- Existing no-allocation behavior still makes zero Cala data calls; manual
  profiles, no-match states, exclusions and deterministic ranking still work.

Phase 2 is ready when:

- Every displayed hop has its own typed evidence and provenance.
- Historical or undated roles do not imply current employment, friendship, deal
  responsibility or willingness to introduce.
- The Cala/Vilynx example displays as a historical network path and identifies
  Kibo's existing-backer context.
- Founder reports remain distinct from public Cala observations, and unconfirmed
  access is visible.
- Optional exploration has independent bounded progress and partial results,
  leaves the original shortlist intact, and respects action exclusions.
- Manual profiles and absent founder/board data remain usable without invented
  nodes, padded results or implied introduction routes.

## 8. Delivery sequence

1. Implement and verify paginated, stage-aware discovery in the backend.
2. Measure real Cala runs against the existing discovery policy using the same
   founder scenarios, comparable source snapshots and reported coverage limits.
3. Expose the improved scope, route evidence and unknowns in the existing UI.
4. Implement the separately bounded founder-network expansion and its typed API
   response, then add graph and chat support for those stored paths.
5. Validate whether a founder can identify a suitable investor and make a
   specific, honest introduction request from the result.

Use the [API audit](cala-connection-audit.md) for observed entity IDs, exact-event
examples, source links, response limitations and the dated investigation record.
