# Branch-basic integration

This branch combines the requested features into one FastAPI backend and one
Next.js frontend. The standalone static app from main is not a second runtime.

Source snapshots:

- `main`: `1fed306569d6da04a6f815e9d6b1664eed7a707c`.
- `branch-one`: `c560eee5d3de079e8c36552cf8b7506f4cb614fc`.

The integration branch merges both histories. Neither source branch is changed.
The feature choices below take precedence over earlier PRD behavior where they
explicitly differ, especially scoring and suggested allocation.

## Feature selection

| Requested feature | Source and resulting behavior |
| --- | --- |
| Setup | Company and lead names first, confirm both Cala identities, then complete sourced prefills and unknown fields inside branch-one's form layout |
| Chatbot | Live model conversation via the configured Codex CLI or model API, alongside main's agent activity trace in the lower-left panel |
| UI | Branch-one's typography, colors, confirmation, progress and three-panel results workspace |
| Knowledge graph UI | Branch-one's interactive exact-event graph, selection, zoom, sources and mobile behavior |
| Scoring | Main's server-side numerical rules and flags, adapted to the typed Cala snapshot |
| Investing percentage | Main's greedy estimated allocation, showing percentages of the open amount and unallocated remainder |
| Copy introduction request | Branch-one's individual/combined clipboard actions, templates and appropriate follow-up wording |

## Company confirmation and round details

Your round combines the original setup and connections steps. It first asks for
only the founder company name and lead investor company name. Cala AI and
Lightspeed are editable test names; no round figures or sector guesses accompany
them. Search returns separate company and investor options with metadata and
sources. Both identities require explicit selection and confirmation.

Only after confirmation does the form show company website, sector and HQ country
from supported Cala fields. Each prefill retains its source references and is
editable. Broad, explicit industry relationships may fill the sector; the app
does not infer it from generic entity type or prose. Unknown fields stay empty.
Founder name, current stage, target and committed amount are not guessed from
historical funding events. The founder completes these fields, reviews the profile
and starts the search directly from the same Your round flow.

Pre-seed, Seed, Series A and Series B remain available, with an empty initial stage.
EUR inputs retain exact numeric and K/M shorthand parsing. Advanced exclusions,
follow-up statuses and preferences remain available after identity confirmation.
Changing companies clears the old confirmation and factual prefills. Expired
matches require another lookup. The backend checks canonical company/lead names
and exact IDs against the confirmed pair before starting a run.

The legacy full-profile `/api/resolve` endpoint still supports its optional lead
detection contract for API clients. The browser uses the identity-first endpoints.
Current-round lead commitment remains `unconfirmed` until the founder changes it.

## Scoring from main

`backend/app/main_scoring.py` ports main's engine with an optional frozen run date.
`backend/app/scoring.py` adapts typed, sourced observations and retains the
branch-one identity, exclusion, provenance and drafting behavior.

| Signal | Weight | Main rule |
| --- | ---: | --- |
| Co-investment | 35 | Shared rounds plus a 0.5 bonus for each recent shared round, divided by three and capped at one |
| Activity | 20 | Observed rounds in the last 365 days, divided by four and capped at one |
| Stage | 15 | Matching stage rounds divided by the profile-round count |
| Sector | 15 | Keyword-matching sector rounds divided by the profile-round count |
| Follows | 10 | Explicit non-lead share among participations with known lead status |
| Geography | 5 | Observed matching-country investment |

Unknown inputs contribute zero; weights are not redistributed. Main's stage and
sector denominators include observations with missing values, so a zero score is
not proof of a verified mismatch. Completeness remains a separate measure of
available evidence. LLM assessments do not select numerical scores.

Main's conflict, inactivity, lead-only, stage and ticket rules populate flags.
Lead-only and stage flags require at least three profile rounds. Red/grey flags
set review status to `flagged`; an amber ticket flag alone remains eligible.
The UI labels these as scoring flags and needs-review status. Historical flags
are not current published mandates or guarantees about an investor's appetite.

Eligible cards precede flagged cards, then main scores determine order with
stable tie-breaks. The branch-one workspace displays up to five sourced cards.
Flagged cards can be inspected; combined introduction requests and suggested
allocations use eligible cards. Explicit founder/mandate exclusions remain
separate from scoring flags.

The adapters retain exact same-event eligibility, deduplicate event IDs, discard
unsupported/future observations and keep the bounded observation windows. These
are data-integrity boundaries, not a second scoring engine.

## Investing percentages

Main estimates a typical ticket by dividing a historical round total by its
investor count and taking the median of those values. The combined implementation
retains that calculation as an explicitly labelled **equal-share heuristic**,
using sourced EUR totals and the observed unique investor count.

It is not an observed investor check, a commitment, a published investment range
or a probability of investing. USD and other totals are not treated as EUR.
The original event total remains labelled as a funding-event total.

Allocation walks eligible cards in rank order, assigns up to the remaining open
amount from each available estimate and leaves an explicit Still open remainder.
Percentages use the open allocation as their denominator. Decimal amounts retain
cents, the rows sum to the open amount and displayed percentages account for the
whole suggested allocation. Missing tickets and flagged investors do not receive
invented allocations. No open allocation produces no search or suggested rows.

The top-investors overview shows each suggested amount and percentage within its
investor row, followed by the Still open remainder and an expandable estimate
basis. This replaces the separate estimated-allocation card.

## Conversation and graph

The lower-left panel provides Chat and Agent activity tabs. Chat sends each
question and recent conversation history to the configured Codex CLI or
OpenAI-compatible model API, together with a bounded snapshot of the run. Selecting
an investor synchronizes the chat context with the graph and shortlist. Generated
replies explain recorded scores, flags, exact-event connections and allocation,
and can help draft an introduction without changing the saved result.

Citations and referenced investor IDs are validated against the supplied evidence.
Unknown fields remain unknown; external tools and new Cala discovery are disabled
for conversation generation. Model errors remain explicit and retryable. The
activity tab shows actual search tool queries, cache use and progress events.

The graph continues to use identified funding-event nodes and explicit
participation/issuer edges. Sector similarity or an inferred allocation does not
create a public relationship. The founder-selected current lead route stays
separate and displays its unconfirmed status where applicable.

## Validation and operation

Backend tests cover original-main golden scoring outputs, typed adapter parity,
allocation arithmetic, auto-detection/identity safeguards, metadata fallback,
statuses, graph evidence, copy drafts and live conversation provider behavior. Frontend
checks cover the combined form, confirmation, main scoring flags/percentages,
graph interaction and clipboard actions, including mobile layout.

Use the existing [backend](../backend/README.md) and
[frontend](../frontend/README.md) start commands. All credentials stay in the
ignored backend environment file. Cala runtime limits, cache timestamps, progress
streaming and partial-result behavior remain active. Both source snapshots remain
available through Git history.
