# RoundCloser HTTP contract v0.1

Base URL: `http://127.0.0.1:8000`. Schemas are generated at `/openapi.json` and
interactive requests are available at `/docs`. Amounts are decimal EUR, serialized
as strings. Country names or common ISO codes are normalized by shared backend
mappings. Do not send API credentials in founder payloads.

## 1. Resolve the lead

`POST /api/resolve`

```json
{
  "profile": {
    "founder_name": "Founder name",
    "startup": {
      "company_name": "Your company",
      "product_description": "Describe the product",
      "sector": "Describe the category",
      "target_customer": "Who buys the product",
      "problem_solved": "What the product solves",
      "country": "Finland",
      "known_competitors": []
    },
    "round": {
      "stage": "seed",
      "target_eur": "2000000",
      "firm_commitments_eur": "1200000",
      "conditional_interest_eur": "100000",
      "lead_status": "firm_verbal",
      "lead_ticket_eur": "1000000"
    },
    "lead_name": "Your actual lead investor",
    "investor_statuses": [],
    "preferences": {
      "include_angels": true,
      "include_corporate_vcs": true,
      "investor_countries": []
    }
  },
  "lookup_startup": false
}
```

Returns `Resolution`: `id`, the founder `profile`, Cala `lead_matches`, optional
`startup_matches`, `resolver_ms`, creation/expiry timestamps and notices. Match
records include the Cala ID, name, type, country, description, website and sources
where available. No company match is necessary for the manual startup flow.

`branch-basic` also accepts the smaller main-branch form through the same typed
contract. `startup.product_description`, `target_customer`, `problem_solved` and
`country` may be empty or omitted. Stage accepts `pre_seed`, `seed`, `series_a`
or `series_b`. Founder websites such as `cala.ai` are normalized to HTTPS.
`excluded_investors` is an optional list of names the founder explicitly wants
excluded. It is separate from current-round commitment/status records.

When `lead_name` is blank, resolution searches the company and attempts bounded
lead detection from an identified event with an explicit lead role. It returns
`detected_lead` with the suggested entity, source company/event IDs, stage,
event date/precision and sources. The resolved `profile.lead_name` is populated
with that suggestion; submit the returned profile after confirmation. Ambiguous
company or lead evidence requires manual refinement. Choosing a different
startup entity invalidates a detected lead tied to the previous company.
The default `round.lead_status` is `unconfirmed`; historical funding does not
establish current-round commitment.

Native Cala matches are optionally enriched after the basic search, within the
same resolver deadline and request budget. Enrichment requests all properties
advertised by that entity's introspection schema, plus available geography and
category relationships. A failed or timed-out metadata read preserves the basic
match and adds a notice; it never changes the identity offered for confirmation.

Entity metadata fields have backward-compatible defaults:

| Field | Meaning |
| --- | --- |
| `entity_type` | Cala's legal/ontology type, such as `Company`, `Organization` or `Person`; separate from investor `kind` |
| `headquarters_address`, `registered_address` | Available headquarters and registration addresses, kept distinct |
| `country_basis`, `country_sources` | How the headquarters country was established, with field-specific source links and dates |
| `kind_basis`, `kind_sources` | Evidence for an investor category; legal company type alone does not establish VC or corporate-VC status |
| `metadata` | All returned property records as `{name, value, sources}`; values retain their JSON types and missing source links stay absent |
| `metadata_status` | `not_requested`, `retrieved` or `unavailable`; `retrieved` describes the property read, not complete graph coverage |
| `metadata_retrieved_at` | Actual retrieval time of the metadata response |

Country extraction uses sourced country properties, headquarters relationships or
an explicitly named country in the headquarters address. Registration location is
never silently used as headquarters location. Website values without a scheme
are normalized to HTTPS when they are valid web addresses. Unsupported investor
categories remain unknown. New native country/category filters use the relevant
field's evidence, rather than unrelated sources from another property. The same
metadata parsing applies to investor profiles in final results.

An investor-status entry has `investor_id` or `investor_name`, and a `statuses`
array. Values: `existing_shareholder`, `firm_committed`, `conditional_interest`,
`already_approached`, `in_conversation`, `declined`, `do_not_contact`. IDs take
precedence over names; do-not-contact wins conflicting statuses.

## 2. Founder confirmation starts the timer

`POST /api/runs` → HTTP 202

```json
{
  "resolution_id": "ID returned by resolution",
  "lead_id": "Cala ID explicitly chosen from lead_matches",
  "profile_confirmed": true,
  "profile": { "...": "the full founder-approved profile from step 1" }
}
```

The abbreviated `profile` above is illustrative; submit the actual complete
`FounderInput`, including any manual startup corrections. A changed lead name
requires resolution again. If matching a startup, set `startup.cala_entity_id` to
an ID offered in this resolution's `startup_matches`; otherwise leave it null for
a manual profile. The founder's own matched company is not flagged as a competing
portfolio business. An unoffered lead/startup ID, missing confirmation, or expired
resolution is rejected. The backend takes an immutable snapshot on confirmation.

If firm commitments meet the target, resolution makes no Cala calls. Confirmation
may use `lead_id: null` and the run ends in `no_open_allocation` without searching.
Conditional interest never reduces the remaining allocation. A conditional lead's
ticket is supplied as conditional context, outside firm commitments.

Response: `id`, `state`, `confirmed_at`, `event_count`, `result_url`, `events_url`.

## 3. Progress and results

`GET /api/runs/{id}` returns the run status.

`GET /api/runs/{id}/events` streams `text/event-stream`:

```text
id: 2
event: tool_call
data: {"id":2,"type":"tool_call","step":"lead_graph","tool":"lead_graph",...}
```

Events include timestamp, step, tool operation, actual query, result count and
cache status when relevant. Reconnect using `Last-Event-ID`; all later events
are replayed. The final `complete` event closes the stream. Heartbeats are SSE
comments. Proxies should disable buffering.

`GET /api/runs/{id}/result` returns HTTP 202 plus `RunStatus` while processing,
or HTTP 200 plus `RunResult` when done. Terminal states are `completed`, `partial`,
`no_match`, `no_open_allocation`, and `failed`. Failed is an integration/runtime
error, not evidence that no co-investors exist. An unconfirmed complete graph
or unfinished profiles makes the result partial.

Results include founder inputs and selected lead, remaining allocation,
lookback, counters, notices, individual candidate cards, explicit exclusions,
incomplete entities, combined draft, timing, Cala tool-call count, cache hits,
retrieval timestamps, scoring version and optional structured error.

Each card contains score components with known/unknown state, weights,
numerators/denominators and sources; completeness separately; distinct shared
round/company counts; up to three shared deals; recent activity; full profile
snapshot; labelled considerations; founder statuses; action and copyable draft.
Source fields distinguish publication date, funding-event date and retrieval time.
Scores prioritize introductions and are not participation probabilities.

`branch-basic` cards additionally contain `review_status` (`short` or `flagged`),
main's `flags`, an optional `estimated_ticket_eur` and its `ticket_estimate_basis`.
Flagged cards remain visible for review; the combined introduction request and
allocation use eligible cards only. `counts` distinguishes shortlisted, flagged
and returned profiles. Explicit founder/mandate exclusions remain separate.

`scoring_version: "main-1fed306"` identifies main's deterministic scoring rules;
unknown signals contribute zero, with completeness reported independently.
`allocation` contains `{investor_id, name, amount_eur, percentage, basis}` rows.
Amounts are decimal strings, percentages refer to the open allocation, and a
`Still open` row retains the remainder. Ticket estimates divide historical EUR
round totals by observed investor counts and take their median. This is an
equal-share heuristic, not a measured individual check; foreign-currency totals
are not silently treated as EUR.

The Cala call counter includes data-operation attempts and retries. MCP session
initialization and cached `tools/list` schema discovery are protocol overhead.
In the native adapter every introspection, entity retrieval and event-join data
call is charged individually. The default graph joins at most three exact events
from a bounded relationship projection, and reports partial coverage. Candidate
activity also uses independently sourced investor event observations; incomplete
issuer profiles do not require invented company identities.
The run uses at most four concurrent operations, ten preliminary candidates and
five eligible detailed profiles; explicit exclusions may trigger bounded backfill.
The 90-second deadline begins at confirmation; the final ten seconds are reserved
for assessments and result assembly. Main's numerical score does not use semantic
ratings; missing numerical inputs contribute zero.

## Result conversation

`POST /api/runs/{id}/chat` explains an existing completed result. Request:

```json
{
  "message": "Compare the top two",
  "history": [],
  "selected_investor_id": null
}
```

`message` is limited to 2,000 characters; `history` accepts at most eight
`{role: "user" | "assistant", content: string}` entries of 4,000 characters each.
The optional selection must identify a shortlisted investor from this run.
Responses include `message`, sourced `citations`, referenced `candidate_ids`, and
`model_used`. The current assistant runs locally over the immutable result and
returns `model_used: false`; no new Cala or foundation-model request is made.
History supports the conversation UI; answers use the current question, selected
investor and saved evidence. Unsupported questions receive scope guidance.

Supported topics include priorities, comparison, shared-event graph paths, saved
introduction/follow-up requests, participation criteria, unknowns, considerations
and exclusions, plus main's flags and estimated allocation percentages. Answers
preserve the recorded ranking and never change founder
statuses or run new discovery. Sources retain their dates. Pending results return
HTTP 409, missing/expired runs 404, invalid selection 400, and invalid input 422.

## Errors and retention

Errors have `{"error":{"code":"...","message":"...","details":null}}`.
Validation uses HTTP 422; missing/expired records 404; invalid selection 400;
capacity 429; unconfigured/unavailable Cala 503; invalid upstream responses 502;
resolution timeout 504. Invalid Cala configuration never silently returns demo data.

Runs expire after the configured retention interval. This MVP has no accounts,
CRM integration, email sending or contact enrichment. The Next.js app consumes
this API; all ranking remains in the backend.
