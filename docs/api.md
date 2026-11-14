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
for assessments and result assembly. Unknown semantic fit uses the neutral score.

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
and exclusions. Answers preserve the frozen ranking and never change founder
statuses or run new discovery. Sources retain their dates. Pending results return
HTTP 409, missing/expired runs 404, invalid selection 400, and invalid input 422.

## Errors and retention

Errors have `{"error":{"code":"...","message":"...","details":null}}`.
Validation uses HTTP 422; missing/expired records 404; invalid selection 400;
capacity 429; unconfigured/unavailable Cala 503; invalid upstream responses 502;
resolution timeout 504. Invalid Cala configuration never silently returns demo data.

Runs expire after the configured retention interval. This MVP has no accounts,
CRM integration, email sending or contact enrichment. The future Next.js app will
consume this API; all ranking remains in the backend.
