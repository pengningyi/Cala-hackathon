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
| Step-one information | Main's basic fields/defaults, optional lead, four stages and already-in investor exclusions, inside branch-one's form layout |
| Chatbot | As clarified by the user, use main's Current understanding explanations and live agent trace in the upper-left panel, with tabs for insights and activity |
| UI | Branch-one's typography, colors, confirmation, progress and three-panel results workspace |
| Knowledge graph UI | Branch-one's interactive exact-event graph, selection, zoom, sources and mobile behavior |
| Scoring | Main's server-side numerical rules and flags, adapted to the typed Cala snapshot |
| Investing percentage | Main's greedy estimated allocation, showing percentages of the open amount and unallocated remainder |
| Copy introduction request | Branch-one's individual/combined clipboard actions, templates and appropriate follow-up wording |

## Form and lead detection

The form asks for founder name, company, website, sector, optional HQ country,
stage, round target, already committed amount, optional lead, investors already
in and known competitors. Its Cala test defaults are a hypothetical EUR 4 million
target and EUR 2 million committed; the lead is blank for detection.

Main's four stages are available: Pre-seed, Seed, Series A and Series B. Amounts
accept EUR numeric or K/M shorthand and are converted to exact decimal amounts.
The omitted product, buyer and problem fields stay empty instead of being
invented. Optional advanced relationship controls preserve follow-up and
do-not-contact behavior.

A blank lead triggers company resolution, then a bounded examination of exact
funding events. Detection requires an identified issuer and a unique, sourced,
explicit lead role. It does not choose an arbitrary fuzzy company match or infer
a lead from a generic investor list. The founder confirms the offered entities.
Missing or ambiguous evidence produces a refinement message.

The detected lead is historical context. Its current commitment defaults to
`unconfirmed`, and introduction requests retain that distinction. Coarse dates
keep their precision rather than presenting an approximate year as an exact day.

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

## Conversation and graph

The current main snapshot contains an agent trace and a Current understanding
summary, not an independent conversational chatbot. The user confirmed that these
are the requested features. The combined results put Current understanding and
Agent activity tabs in the upper-left panel using branch-one's visual layout.
The explanations use the recorded score, flags, counts and open allocation; the
trace shows actual tool queries, cache use and progress events. Selecting an
investor synchronizes the explanation context with the graph and shortlist.
The older local result-conversation API remains compatible for API clients;
it is not the primary upper-left UI on this branch.

The graph continues to use identified funding-event nodes and explicit
participation/issuer edges. Sector similarity or an inferred allocation does not
create a public relationship. The founder-selected current lead route stays
separate and displays its unconfirmed status where applicable.

## Validation and operation

Backend tests cover original-main golden scoring outputs, typed adapter parity,
allocation arithmetic, auto-detection/identity safeguards, metadata fallback,
statuses, graph evidence, copy drafts and local conversation answers. Frontend
checks cover the combined form, confirmation, main scoring flags/percentages,
graph interaction and clipboard actions, including mobile layout.

Use the existing [backend](../backend/README.md) and
[frontend](../frontend/README.md) start commands. All credentials stay in the
ignored backend environment file. Cala runtime limits, cache timestamps, progress
streaming and partial-result behavior remain active. Both source snapshots remain
available through Git history.
