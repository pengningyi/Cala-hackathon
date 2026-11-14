# RoundCloser — MVP PRD

Nov 14, 2026 · @Maria

## Overview

A founder who already has a lead investor enters their round details and receives up to five relevant co-investors connected to that lead, with Cala evidence, considerations and a copyable introduction request. The processing target is 90 seconds after entity confirmation.

**Problem.** Once a lead commits, the founder still has to fill the rest of the round. They need to turn the lead's investment network into specific introduction requests while understanding stage, sector, participation-size and portfolio-overlap considerations.

**Target user.** A seed or Series A founder in Europe with a signed or firm verbal lead commitment and an open allocation of roughly 20–50% of the round. A founder with conditional lead interest may explore candidates, but the result and draft must describe the lead as provisional.

**Job to be done.** "My lead is in. Which investors should I ask them to introduce, why are those introductions relevant, and what should I ask?"

**Why Cala.** The core signal is relational: which investors have co-invested with this lead, how recently, across which companies and at which stages. Cala supplies the connected company, round, investor and source data that drives discovery and ranking.

## Design principles

1. **Trust Cala.** Cala is the source of truth for public facts in the MVP. Use its returned entities, relationships, facts, source links and dates directly. An independent source audit or additional web-research agent is outside P0.
2. **Lead-connected.** Main-shortlist candidates must have participated with the selected lead in the same identified funding round. Investing in the same company in different rounds does not establish this connection.
3. **Explain the recommendation.** Every factual reason and consideration links to a Cala source and date. Sector similarity and competitive overlap are labelled LLM assessments supported by those facts.
4. **Say "unknown", never guess.** Missing fields remain unknown. The LLM does not fill gaps from memory, and total round size is never presented as an individual investor's check.
5. **Considerations inform a decision.** Historical lead behavior, low recent activity, typical ticket size and portfolio adjacency are advisory. Explicit founder exclusions and incompatible published mandates can remove a candidate.
6. **One complete action.** The P0 outcome is a copyable introduction request to the lead. There are no accounts, CRM integrations or automatic email sending.
7. **Show progress.** A live trace shows tool actions, retrieved results, cache use and the current step. Sources remain available on the final cards.

## User flow

1. **Fill the form.** Enter the startup profile, round details, primary lead and optional investor statuses.
2. **Confirm the lead and startup profile.** Show Cala matches with identifying details. The founder confirms the correct lead. Company lookup is optional; if the startup has no match, use the founder's manual profile.
3. **Watch the agent run.** Start the processing timer after confirmation. Stream progress while retrieving and profiling candidates.
4. **Read the result.** Show up to five lead-connected investors, ranked by introduction priority, with reasons, shared deals, considerations, source links and profile completeness. Zero to four results are valid; do not pad the list.
5. **Act.** Copy an individual introduction request or a combined request for the top two or three candidates. Already-contacted candidates get an appropriate follow-up suggestion instead of a fresh introduction request.
6. **Adjust.** The founder may mark a candidate do-not-contact or correct their own startup profile and rerun.

## Features

P0 must work end to end by the demo. P1 is considered only after P0 passes its acceptance criteria.

### Input form

| Field | Required | Why we need it |
| --- | --- | --- |
| Founder name | Optional | Signature in the introduction draft |
| Company name | Yes | Startup identity and optional Cala lookup |
| Company website | Optional | Disambiguates an existing public company |
| Product description / sector | Yes | Sector fit |
| Target customer and problem solved | Yes | Product relevance and portfolio-overlap assessment |
| Country (HQ) | Yes | Geography fit |
| Round stage (seed, Series A) | Yes | Stage fit |
| Round size target (€) | Yes | Computes the remaining allocation |
| Firm commitments (€), including any firm lead commitment | Yes | Amount the founder considers committed; exclude conditional interest |
| Conditional interest (€) | Optional | Display separately; does not reduce the remaining allocation |
| Primary lead investor name | Yes | Anchor of the search and intended introduction route |
| Lead commitment status | Yes | Signed, firm verbal or conditional; controls wording in results and drafts |
| Lead's ticket (€) | Optional | Context for the request; identify whether firm or conditional |
| Investor statuses | Optional | Existing shareholder, firm committed this round, conditional interest, already approached, in conversation, declined this round or do-not-contact |
| Known competitors | Optional | Adds founder context for overlap assessment |
| Preferences: include angels, include corporate VCs, geography | Optional | Explicit founder filters |

**Validation and status rules**

- Round target must be positive; amounts must be non-negative and in EUR.
- Remaining allocation = max(0, target − firm commitments). Show conditional interest separately.
- If firm commitments meet or exceed the target, show "No open allocation" and stop the co-investor search.
- A conditional lead ticket is not included in firm commitments. If a firm lead ticket is entered, it must not exceed total firm commitments.
- An existing shareholder remains eligible for a follow-on unless committed this round or otherwise excluded.
- Firm committed this round, declined this round and do-not-contact statuses exclude a candidate. Do-not-contact takes precedence over other statuses.
- Already approached, in conversation and conditional interest remain eligible, but their status is shown and the suggested action reflects it.
- Recognized aliases or vehicles belonging to the primary lead are not separate co-investor recommendations. Use Cala organization relationships where available.
- The P0 flow uses one primary lead. Co-lead support can follow after the MVP.

### Feature list

| ID | Feature | Priority |
| --- | --- | --- |
| F1 | Input form, manual startup profile and status validation | P0 |
| F2 | Resolve and confirm the lead; optionally match the startup in Cala | P0 |
| F3 | Discover lead-connected co-investors, with shared-round and distinct-company counts | P0 |
| F4 | Generic sector/stage discovery in a separate list without implying a lead introduction path | P1 |
| F5 | Bounded candidate profiles: recent activity, stage/sector/geography fit, historical roles, available ticket criteria and relevant portfolio companies | P0 |
| F6 | Advisory considerations and explicit exclusions; no severity levels | P0 |
| F7 | Normalized deterministic fit score with sourced reasons and separate profile completeness | P0 |
| F8 | Result cards, explicit exclusion reasons and partial/no-match states | P0 |
| F9 | Live progress trace showing tool actions, queries and cache use | P0 |
| F10 | Copyable introduction request to the lead, per candidate and for the top two or three | P0 |
| F11 | CSV export | P1 |
| F12 | Allocation planning, only where applicable investor-level participation ranges are available | P1 |
| F13 | Partner-level introduction paths where Cala explicitly provides the relationship | P1 |
| — | Accounts, saved searches, CRM sync, sending emails and investor contact enrichment | Out of scope |

## Agent system

One orchestrator controls five steps. Cala supplies public facts; the LLM chooses targeted queries, assesses semantic fit and writes the introduction requests. Code controls budgets, status rules and ranking. Separate agent processes are optional.

    Founder profile → Resolver → Lead graph → Candidate profiles → Score + considerations → Introduction plan
                         ↓
                  Founder confirms lead

After resolution, pause until the founder confirms the lead and startup profile. The timer starts when they confirm.

| Step | Input | Cala calls | Output |
| --- | --- | --- | --- |
| 1 Resolver | Form fields | Entity search for the lead; optional startup lookup | Confirmed lead ID and founder-approved startup profile |
| 2 Graph Explorer | Lead ID and founder filters | Lead's rounds → participants | At most 10 preliminary candidates connected through shared rounds |
| 3 Profiler | Preliminary candidates and startup profile | Bounded, parallel retrieval for the five strongest preliminary candidates | Activity, fit inputs, known lead roles, available ticket criteria, relevant portfolio overlaps and source references |
| 4 Score + considerations | Profiles, founder statuses and preferences | None | Final fit scores, profile completeness, advisory considerations and explicit exclusions |
| 5 Writer | Retrieved facts, labelled assessments, computed scores and sources | None | Up to five cards and copyable introduction requests |

**Discovery and profiling rules**

- Use a configurable 60-month lookback for shared investments with the lead. Show the lookback in the result.
- Use each Cala funding event once when counting shared rounds; repeated source references do not increase the count.
- Count shared rounds and distinct shared portfolio companies separately.
- Apply founder exclusions, investor-type preferences and explicit mandate restrictions before selecting candidates for deep profiling.
- Select at most 10 preliminary candidates using shared-company breadth, shared-round recency and readily available stage/sector context.
- Enrich at most five in parallel. If an exclusion appears during profiling, backfill from the preliminary list only while time and request budgets remain.
- Profile investment behavior over the last 24 months, with the last 12 months reported separately.
- A published initial check range, minimum, typical historical check and total round amount are different fields; retain their labels.
- Portfolio-overlap assessment compares product, target buyer and problem solved. Same-sector membership alone is an expertise signal.

**Runtime budget and fallback behavior**

- Default limits: 10 preliminary candidates, five detailed profiles, four concurrent Cala requests and 20 Cala calls per confirmed run. All are configurable for the event's rate limits.
- The global processing deadline is 90 seconds after confirmation. Reserve the final 10 seconds for result assembly and drafting.
- Prefer focused entity/relationship retrieval and batching where supported. Keep broad knowledge searches targeted and bounded.
- Retry a transient failure at most once, only within the remaining call/time budget; respect any retry-after response.
- Cache real Cala responses with retrieval timestamps. Clearly label cached results in the trace and final result.
- At the deadline, return available cards with a partial-result notice. Unfinished profiles are shown as pending/incomplete, not rejected.
- If the writer cannot finish in time, generate a copyable template from the retrieved shared deals and founder fields.
- If no eligible lead-connected candidate is found, show the search scope and a no-match state. Do not insert generic investors into the main shortlist.

## Architecture and development sequence

**Separate frontend and backend.** Keep two independently runnable applications in the same repository: backend/ for Python/FastAPI and frontend/ for Next.js. Each has its own dependencies, configuration and start command. They communicate through a documented HTTP/JSON API, with server-sent events for progress.

- **Backend ownership:** input validation, Cala integration, entity resolution, the agent pipeline, scoring, considerations, exclusions, introduction drafts, caching, runtime budgets and result storage for an active run. API credentials stay on the backend.
- **Frontend ownership, in the later UI phase:** the input form, entity-confirmation screen, progress display, result cards, source presentation and copy actions. The frontend consumes backend results; it does not duplicate ranking or agent logic.

**Build the backend first.** Complete and exercise the backend through API documentation, curl or a CLI before starting frontend scaffolding, visual design or UI implementation. The product screens described elsewhere in this PRD belong to the later UI phase.

**API contract first.** Define request/response schemas for resolving the lead, submitting the founder-confirmed lead and startup profile, starting a run, consuming progress events and retrieving the final result. Include structured errors, partial/no-match states, sources, considerations, exclusions and copyable draft text. Founder confirmation must be possible through the API without a UI.

### Backend readiness gate

UI design and development begin only after the backend can:

1. Accept a manual startup profile, resolve the lead and receive the confirmed selection through the API.
2. Complete the Cala-to-recommendation pipeline and return up to five candidate records, normalized scores, considerations, sources and introduction drafts.
3. Handle the status, no-open-allocation, no-match, timeout and partial-result cases defined in this PRD.
4. Run the backend acceptance scenarios and report timing, request counts and structured outputs without any frontend.

After this gate passes, design the UI around the working API and implement the Next.js frontend. Validate the final browser flow after integration.

## Scoring and considerations

Ranking is deterministic code over a fixed profile snapshot. The LLM supplies labelled sector/overlap assessments and text; it does not choose numeric scores or change weights during a run. Freeze the default weights before testing multiple demo scenarios.

### Fit score (0–100)

Every component is normalized to 0–1. The fit score is a prioritization heuristic, not a probability that the investor will join.

| Signal | Weight | Normalized computation |
| --- | --- | --- |
| Co-investment with the lead | 35 | For each distinct shared company, use the most recent shared round. Sum its recency weight, capped at 3, then divide by 3 |
| Recent activity | 20 | min(rounds in the last 12 months / 4, 1) |
| Stage fit | 15 | Matching-stage rounds / rounds with known stages in the 24-month profile |
| Sector fit | 15 | Mean fit across evaluated portfolio companies: 1 for the same category, 0.5 for an adjacent category and 0 for unrelated; unknown classifications do not enter the denominator |
| Historical non-lead participation | 10 | Explicit non-lead participations / participations with known lead status in the 24-month profile |
| Geography fit | 5 | 1 for a prior investment in the founder's country; 0.5 for the same predefined region; otherwise 0 when geography observations exist |

**Computation rules**

- Recency weight = 2 raised to (−age in months / 24). Use the run date and funding-event date, not source retrieval date.
- Fit score = sum(weight × component value), rounded to the nearest whole number.
- If a component cannot be computed, retain an unknown status and use 0.5 as its neutral scoring value. Do not silently redistribute its weight.
- Profile completeness = the sum of weights for known components divided by 100. Display it separately from fit; it describes available profile fields, not investment likelihood or source reliability.
- Show counts and denominators behind percentage-based components. A zero denominator produces unknown.
- Missing lead status is unknown, not a non-lead participation. Total round size is not used as an individual check.
- Use stage and region mappings shared across all candidates. Persist labelled sector assessments with the profile snapshot so the same snapshot yields the same ranking.
- Break ties by co-investment component, then most recent shared-round date, then normalized investor name and Cala ID.
- Shared-company breadth is capped so repeated follow-ons in a single company cannot dominate the connection component.
- None of these historical score components alone creates an exclusion.

### Advisory considerations

Considerations stay on the investor's card. They do not automatically remove a candidate and have no severity column or color-coded do-not-pitch label.

| Consideration | Rule and displayed interpretation |
| --- | --- |
| Portfolio overlap | Show the specific portfolio company, source facts and an assessment of adjacent business, possible overlap or direct substitute. A founder-listed competitor is labelled as founder-supplied context; the founder may block the investor |
| Low recent activity | No investment found in the last 12 months: display "No recent investment observed; confirm current appetite" |
| Usually leads | More than 80% of participations with known roles were led: show the numerator/denominator and ask whether the investor would participate alongside this lead |
| Limited stage history | Fewer than 10% of known-stage rounds match: show the share and ask about the current mandate |
| Participation size to confirm | Typical check exceeds the gap, or available ranges are broad: show the published/historical range and ask whether a smaller participation is possible |
| Existing relationship | Show existing shareholder, approached, in-conversation or conditional-interest status and adapt the next action |

### Explicit exclusions

Exclude the lead itself, recognized lead aliases/vehicles, founder-marked do-not-contact investors, investors declined or firmly committed in this round, and candidates that violate an explicit founder filter or applicable published mandate. A published minimum initial investment above the remaining allocation can be an exclusion; a typical check above it cannot.

List each excluded candidate with its reason. Existing cap-table membership, inferred competition, historical lead share and low recent activity are not automatic exclusions. Historical mandate information without clear current applicability stays a consideration.

The 80%, 10% and 12-month consideration thresholds are adjustable constants, independent of the ranking weights.

## Output

One result page contains the lead-connected shortlist, considerations on each card, explicit exclusions with reasons, and any partial/no-match notices.

### Shortlist card (up to five investors)

- **Name, type and country**.
- **Fit score**, **profile completeness** and a one-line explanation.
- **Connection to the lead**: shared-round count, distinct-company count and up to three shared deals with stage/date.
- **Activity**: observed rounds in the last 12 months and most recent known funding-event date.
- **Fit**: stage mix, relevant company examples, geography and clearly labelled ticket criteria or "unknown".
- **Considerations**: specific overlap or participation questions, with facts and assessments distinguished.
- **Sources**: Cala links and dates behind factual reasons.
- **Action (P0)**: copy the introduction request or an appropriate follow-up draft.

### Introduction request

Reference the founder's company, stage, remaining allocation, the named candidate and one or two shared investments. Ask the lead to confirm whether an introduction is appropriate. Do not claim a personal relationship, current investor interest or a guaranteed ticket amount.

Example: "Could you introduce us to Z? Cala shows you invested together in A's seed round in June 2025. We are raising a seed round for [company], with €[amount] remaining. Would Z be appropriate to approach?"

For conditional leads, describe the lead as provisional. For investors already approached or in conversation, request help with the existing conversation instead of a duplicate introduction. The combined request includes at most three prioritized candidates.

### Header summary

- Remaining allocation = max(0, target − firm commitments).
- Firm commitments and conditional interest displayed separately.
- Selected lead, lead commitment status and search lookback.
- Counts: discovered, profiled, shortlisted, excluded and incomplete.
- Cala retrieval timestamps, cache status and whether the result is partial.

## Success criteria, demo and build plan

### Acceptance criteria

- The backend runs independently and exposes the complete workflow through its API without requiring the frontend.
- Backend acceptance and the readiness gate pass before frontend scaffolding, UI design or UI implementation begins.
- Measure resolver latency separately. Target backend processing within 90 seconds after the confirmed selection is received through the API, recording cold-cache and warm-cache timings, call counts and usable-result rates.
- All main-shortlist candidates share an identified round with the confirmed lead. Do not pad to five or mix in generic candidates.
- Every factual reason and consideration includes a Cala source link/date; inferred fit and overlap are labelled.
- Every completed card has a copyable introduction or follow-up request in P0.
- The same frozen profile snapshot produces the same scores, order and exclusions.
- Existing shareholders remain eligible unless another exclusion applies. Firm-committed, declined and do-not-contact statuses behave as specified.
- Advisory considerations do not remove candidates. Explicit minimum-ticket or geography mandates only exclude when applicable.
- A startup absent from Cala can complete the manual-profile flow.
- No-open-allocation, no-match, sparse-result, timeout and partial-result states work without inventing facts or candidates.
- Founder feedback identifies at least one candidate/request they would act on; track this separately from technical correctness.

### Demo scenarios

Use Cala as the primary example and at least two additional founder profiles with different leads, stages or countries. Freeze scoring weights before evaluating those scenarios.

Cover a strong shared-investment connection, a direct product overlap, an adjacent business with no automatic exclusion, and a case with no relevant overlap. Also exercise an existing shareholder, an already-contacted candidate and a sparse-result case. A demo is not required to manufacture a conflict.

Record ten confirmed runs across the scenarios, reporting median and p95 processing time, cold/warm cache status, Cala call count, result completeness and whether the introduction request is usable. This evaluates the recommendation workflow, not an independent audit of Cala.

### Build order

1. Define the separate backend/ and frontend/ application boundaries and document the backend API schemas. Defer frontend scaffolding and UI design.
2. Build a backend-only vertical slice: manual profile → API-confirmed lead → shared rounds → one candidate → introduction draft returned as structured data.
3. Complete the backend with bounded five-candidate profiling, normalized scoring, investor statuses, considerations, source metadata, progress events, caching and partial/no-match handling.
4. Exercise the backend through API documentation, curl or a CLI. Run the backend acceptance scenarios and timing checks, and pass the backend readiness gate.
5. Only after the backend works, design and build the Next.js frontend: form, confirmation, progress display, result cards, source links and copy actions, using the established API.
6. Validate the integrated browser flow and remaining UI-specific acceptance criteria. Consider P1 features only after P0 works end to end.
