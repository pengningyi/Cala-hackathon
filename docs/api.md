# RoundMe HTTP contract v0.1

Base URL: `http://127.0.0.1:8000`. Schemas are generated at `/openapi.json` and
interactive requests are available at `/docs`. Amounts are decimal EUR, serialized
as strings. Country names or common ISO codes are normalized by shared backend
mappings. Do not send API credentials in founder payloads.

## 1. Match company identities, then complete the round

`POST /api/onboarding/matches` accepts only names:

```json
{"company_name":"Cala AI","lead_name":"Lightspeed Venture Partners"}
```

Both names are required. Returns `CompanyMatches`: `id`, `startup_matches`,
`lead_matches`, `resolver_ms`, `created_at`, `expires_at` and `notices`. Cala
uses the owner's REST knowledge query for matching, then MCP entity introspection
and retrieval for all advertised property metadata. REST matching has a shared
40-second default window; optional MCP enrichment has a shared 15-second default
window afterward. Failed enrichment preserves selectable basic matches with an
unavailable status and a notice. No founder round, funding discovery or model assessment is required.
Neither the first match nor a single result is implicitly confirmed.

`POST /api/onboarding/matches/{id}/confirm` accepts exact offered IDs:

```json
{"startup_id":"chosen-company-id","lead_id":"chosen-lead-id"}
```

Returns `CompanyConfirmation`: `resolution_id`, `startup`, `lead`, `prefill`,
`prefill_sources`, `expires_at`, and `notices`. `prefill` contains canonical
`company_name`, `website`, `sector` and `country`; unsupported fields are empty
strings. `prefill_sources` maps field names to the original dated source records.
Sector uses explicit sourced industry/sector metadata. The owner's headquarters
country field is prefilled only from headquarters evidence. Office/registration
fallbacks remain visible in entity metadata with their original field sources;
they do not silently establish founder headquarters. Descriptions, generic entity types and historical
funding stages do not fill current founder facts.

The user edits supported prefills and supplies missing profile fields, stage,
target and committed amount. Submit the completed `FounderInput` through
`POST /api/runs` with this `resolution_id`, the confirmed `lead_id`, and
`profile_confirmed: true`. The company Cala ID and canonical company/lead names
must match the confirmed pair. Changed identities require a fresh confirmation;
expired matches or confirmations return HTTP 404. An empty current allocation
still skips the funding-discovery run, although company matching happened earlier.

### Legacy full-profile resolution


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
| `headquarters_address`, `office_address`, `registered_address` | Available headquarters, office and registration addresses, kept distinct |
| `country_basis`, `country_sources` | Headquarters evidence or a labelled address fallback, with field-specific source links and dates |
| `kind_basis`, `kind_sources` | Evidence for an investor category; legal company type alone does not establish VC or corporate-VC status |
| `metadata` | All returned property records as `{name, value, sources}`; values retain their JSON types and missing source links stay absent |
| `metadata_status` | `not_requested`, `retrieved` or `unavailable`; `retrieved` describes the property read, not complete graph coverage |
| `metadata_retrieved_at` | Actual retrieval time of the metadata response |

Country extraction first uses sourced country properties, headquarters relationships
or the headquarters address. A sourced office address supplies a fallback when
headquarters country is unavailable; registration is considered only if no office
address is present. Conflicts and ambiguous or unsourced addresses remain unknown.
A terminal US state and ZIP pair can establish an address-country proxy; a city
or bare state code cannot. Metadata display and chat retain the fallback basis;
the founder enters actual headquarters if only a fallback is known. The owner's portfolio Country-match rule receives a separate
headquarters-only data projection, so office and registration fallbacks do not
become headquarters scoring evidence. Website values without a scheme
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
  "resolution_id": "ID returned by company confirmation or legacy resolution",
  "lead_id": "Cala ID explicitly chosen from lead_matches",
  "profile_confirmed": true,
  "profile": { "...": "the completed founder-approved round profile" }
}
```

The abbreviated `profile` above is illustrative; submit the actual complete
`FounderInput`, including edited company details and current-round fields.
Identity-first confirmation requires the exact confirmed company Cala ID and
canonical company/lead names. A different pair requires matching and confirmation
again. The legacy `/api/resolve` flow also permits a manual startup without a Cala
ID; supplied IDs must come from that resolution. The founder's own matched company
is not flagged as a competing portfolio business. Unoffered IDs, missing review
confirmation and expired tokens are rejected. The backend takes an immutable
profile snapshot when the reviewed run starts.

Firm commitments meeting the target produce `no_open_allocation` without funding
discovery. Identity-first company matching still happened before these amounts
were entered. Legacy full-profile resolution can skip matching too and accept
`lead_id: null` when no allocation remains.
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

`people` is a default-empty list of already retrieved, source-backed `Role`
records for graph display. Each record preserves the person and organisation
entities, role kind, `valid_since`, `valid_until`, explicit `current` flag and
the role's own sources. It includes available founder, lead-fund, candidate-fund
and intermediate organisation roles without additional Cala calls. Duplicate
roles merge only when person ID, organisation ID, kind, dates and current flag
all match; names alone never merge identities. Source publication/retrieval dates
do not establish when a role was held, and `current: false` is not a claim of a
current affiliation. Empty or partial lists describe retrieved evidence rather
than complete coverage. A founder name entered in the form does not create a
verified person node. Trust-loop contacts can be companies, so clients must not
render every `loop.contact` as a person. This display snapshot does not change
scores, rankings or allocations, and older results without `people` remain valid.

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

`scoring_version: "rules-2026-11"`: one rule set (`app/rules.py`) is used for both
scoring and ranking. Each rule is met (1 point), not met (0) or not checked (no
Cala data; 0 points). `fit_score` is the number of rules met. `components` holds
one entry per rule: `name` is the rule ID, `value` 1 or 0, `known` false when not
checked, `group` one of Connection / Activity / Fit / Risk, plus an explanation
and sources.

| Rule | ID | Met when |
| --- | --- | --- |
| Lead co-investor | `lead_coinvestor` | At least one shared round with your lead |
| Repeat partner | `repeat_partner` | Shared rounds with your lead in two or more companies |
| Recent partner | `recent_partner` | A shared round with your lead in the last 24 months |
| Your investor | `your_investor` | Invested in one of your company's own rounds |
| Backed your founder | `backed_your_founder` | Backed a company a founder previously founded |
| Warm path | `warm_path` | At least one trust loop links you to the investor |
| Lead-closed path | `lead_closed_path` | A trust loop that also runs through your lead |
| Recently active | `recently_active` | Invested in the last 12 months |
| Stage match | `stage_match` | At least 25% of rounds with a known stage (dated in 60 months, or undated) are at your stage |
| Sector match | `sector_match` | Backed a portfolio company whose Cala industry matches your sector |
| Country match | `country_match` | Backed a portfolio company headquartered in your country |
| No competitor | `no_competitor` | Has not backed a competitor you named (failing it also flags the card) |

Ranking: most rules met, then most Connection rules met, then the most recent
sourced round, then name and ID. Action: Your investor -> `reach_out_directly`;
Warm path -> `reach_out_via_contact`; Lead co-investor -> `ask_lead_intro`;
otherwise `skip` (shown as low priority). Rules dropped because Cala lacks the
data (lead/follow roles, EUR ticket sizes, deployment counts) and the earlier v2
signals (reciprocity, dry powder, momentum, next-round record, partner departure,
set selection and composition) are no longer computed. Older saved results may
carry `main-1fed306` or `v2-trust-2026-11`.

Each card also has:

| Field | Meaning |
| --- | --- |
| `trust` | Reachability 0-1 from public professional roles, separate from `fit_score`; `null` when no people data exists |
| `loops` | Up to three `{id, kind, contact, hops[], strength, confirmed, label}`; kinds `known`, `one_contact`, `multi_contact`, `lead_loop` |
| `recommended_action` | From the rules: `reach_out_directly`, `reach_out_via_contact`, `ask_lead_intro` or `skip` |
| `action_contact` | The common contact for `reach_out_via_contact` |
| `origins` | Why the investor is a candidate: `lead_network`, `existing_investor` (in your company's own rounds) and/or `previous_company_backer` |
| `origin_rounds` | Up to four sourced rounds of your company or a founder's previous company that the investor joined |

A shared round with the lead is no longer required. Candidates come from the
lead's shared rounds, your company's own identified rounds (existing investors;
action `reach_out_directly`, follow-up draft written to the investor) and the
rounds of companies the founders previously founded. Founder-network cards with
no shared round get a direct draft; the combined request to the lead lists only
lead-connected cards.

Trust loops have at most four edges, with your company and its founders as one
home node: `existing_investor` (fund invested in your company), `previous_company`
(founder founded a company the fund backed), `known` / `one_contact` /
`multi_contact` (people), and `lead_loop` (from the lead partner). When the fund
co-invested with your lead, `closes_through_lead` is true, the closing hops
(`co_invested`, `led`) are included, and the loop's contribution is multiplied by
`1 + co_invested strength`. Hop `edge_type` adds `invested_in` and `founded`.

Loops come only from sourced, dated roles with overlapping validity windows and
never imply a personal relationship. A founder can confirm a loop by rerunning
with its ID in `profile.confirmed_loop_ids`. The optional `profile.lead_partner_name`
enables lead-partner loops; `profile.founder_person_ids` skips the founder lookup.

Up to eight profiled candidates are checked; the top five by the ranking order
above are returned. Sector and Country match read industry and headquarters for
up to six of the most-shared portfolio companies (`COMPANY_CONTEXT_LIMIT`).
`allocation` contains `{investor_id, name, amount_eur, percentage, basis}` rows.
Amounts are decimal strings, percentages refer to the open allocation, and a
`Still open` row retains the remainder. Ticket estimates divide historical EUR
round totals by observed investor counts and take their median. This is an
equal-share heuristic, not a measured individual check; foreign-currency totals
are not silently treated as EUR.

The Cala call counter includes data-operation attempts and retries. MCP session
initialization and cached `tools/list` schema discovery are protocol overhead.
In the native adapter every introspection, entity retrieval and event-join data
call is charged individually. The default graph joins at most six exact events
from a bounded relationship projection, and reports partial coverage. Candidate
activity also uses independently sourced investor event observations; incomplete
issuer profiles do not require invented company identities.
The run uses at most six concurrent operations, 72 Cala calls, fifteen preliminary
candidates and eight detailed profiles, from which five are selected; explicit
exclusions may trigger bounded backfill. People and fund edges ride on the same
investor projections. Wave 0 runs the lead graph, startup founders and the
lead-partner search in parallel; wave 1 runs candidate profiles alongside up to
three person-role lookups and four organisation people lookups; your company's
last three rounds are read in wave 0, and up to two previous companies' last two
rounds each in wave 2, after which two reserved profile slots go to their backers. Budget exhaustion
leaves the affected rules not checked and never blocks the shortlist. A `loops`
progress event reports the measured loops before `complete`.
The 120-second deadline begins at confirmation; the final ten seconds are reserved
for assessments and result assembly. The numerical score does not use semantic
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
`model_used`. Successful runtime replies use the configured Codex CLI or
OpenAI-compatible API and return `model_used: true`. Each question triggers a
model request with the immutable result, selected investor and bounded recent
conversation history. In snapshot-only mode no new Cala query is made. The backend resolves returned
citation references and investor IDs against the supplied evidence; the model
cannot add arbitrary source URLs or replace source dates.

Supported topics include priorities, comparison, shared-event graph paths, saved
introduction/follow-up requests, participation criteria, unknowns, considerations
and exclusions, plus main's flags and estimated allocation percentages. Answers
preserve the recorded ranking and never change founder
statuses. Live retrieval has a request-scoped budget and never changes the saved discovery
result. After a successful validated chat answer, actual sourced tool observations are
retained in a separate, bounded per-run research journal for report generation. Generated
assistant statements are not evidence; failed/cancelled answers do not commit observations.
Sources retain their dates. Pending results return
HTTP 409, missing/expired runs 404, invalid selection 400, and invalid input 422.
Disabled or unavailable model configuration returns an explicit error rather
than a successful canned response. Generation deadlines and concurrent capacity
are controlled by `CHAT_TIMEOUT_SECONDS` and `CHAT_MAX_CONCURRENCY` separately
from discovery. Provider failures, invalid output and timeouts use the standard
error envelope so the UI can retain the question and offer a retry.

### Cala tool-calling agent

With `MODEL_PROVIDER=openai_compatible`, a configured Cala adapter and
`CHAT_AGENT_ENABLED=true` (the default), chat is an agent
(`backend/app/chat_agent.py`). The model may call bounded Cala tools while
answering: `search_entities`, `get_entity_details`, `get_investor_profile`,
`get_funding_round` (issuer, date, stage and all participants of one round),
`get_company_funding_rounds`, `get_organisation_people`, `get_person_roles` and,
when `CHAT_KNOWLEDGE_SEARCH=true`, the slow `knowledge_search`. It ends with a
`final_answer` tool call. Lookups go through the same cache, retry and budget
code as discovery, capped per question by `CHAT_CALA_CALL_LIMIT` (24) and
`CHAT_MAX_TOOL_ROUNDS` (6). The last 30 s of `CHAT_TIMEOUT_SECONDS` (90) is
reserved for writing the answer. Sources returned by tools join the citation
catalog, so answers can cite them under the same server-side validation. Tool
findings are labelled as live lookups and never change the saved result. The
Codex provider keeps snapshot-only chat.

With `MULTI_AGENTS_ENABLED=true` (default), chat instead exposes only
`request_research` and `final_answer`. A separate Research Agent receives a
focused question and known IDs, chooses among the Cala tools above, and returns
validated cited findings, server-registered observations and unknowns. Both loops
share the same retrieval deadline, raw Cala-call allowance and 12-call model
budget (`AGENT_MODEL_CALL_LIMIT`), with one final chat call reserved. Research is
limited by `RESEARCH_MAX_TOOL_ROUNDS=3` and `RESEARCH_MAX_MODEL_CALLS=5` per
delegation. Set `MULTI_AGENTS_ENABLED=false` for the original direct-tool loop.

`POST /api/runs/{id}/chat/stream` takes the same request and returns
`text/event-stream`. It sends one `step` event for each lookup state change,
with fields `{id, tool, label, arguments, status: started|done|failed, count, error}`.
An optional `agent` field attributes research/chat activity. Delegation steps use
`tool: "request_research"`; they are agent activity, not additional raw Cala calls.
The stream ends with one `answer` event (a chat response) or one `error` event
(`{code, message, status}`). Run-readiness and selection errors are returned as
normal JSON errors before the stream starts. Responses from both endpoints
include `tool_steps`, the lookups used for that answer.

Successful chat responses also include `evidence_revision`, the available per-run research
revision. Duplicate observations with unchanged source/content do not increment it.
`GET /api/runs/{id}/evidence` returns a read-only freshness status:

```json
{"revision": 2, "observation_count": 3, "updated_at": "2026-11-17T12:00:00Z", "truncated": false}
```

There are no model/Cala calls for this check, and no client evidence-write endpoint.
Journals share the run's in-memory retention and restart behavior, retain at most 32
observations/60 KB, and mark truncation when older observations are evicted.

### Founder report

`POST /api/runs/{id}/report` invokes the separate Report Agent:

```json
{"history": [{"role": "user", "content": "Focus on next steps for the top investors."}]}
```

History is optional, limited to eight messages and 4,000 characters per message.
It supplies conversation topics only; factual claims and citations must come from
the saved result or the run's server-recorded Cala research observations. Transient
chat source IDs do not become report evidence merely because they appear in history.
The server captures an isolated research snapshot when generation starts and remaps
its source references into the report catalogue. The response has the same `ChatResponse`
shape as chat, with Markdown narrative, validated citations and `evidence_revision`
identifying that exact snapshot. Findings arriving during generation make this report
outdated rather than silently changing its evidence. Clients cannot submit observations
or choose the authoritative revision through the report request.

The frontend retains deterministic snapshot facts, actions and ranking alongside the
narrative, caches successful reports within the workspace and shows "New findings available"
with an explicit "Update report" action when a newer revision exists. A failed update keeps
the previous report/export available. Reopening a successful report does not generate again.

Unknown/not-ready runs return 404/409. Disabled providers return
`report_disabled` (503), busy generation `report_busy` (429), invalid output
`report_invalid_output` (502), and timeout `report_timeout` (504). Browser
disconnect cancels generation. `REPORT_TIMEOUT_SECONDS=45` leaves headroom under
the frontend's 60-second proxy limit. At most one output-validation repair is
attempted. Report copy/download remains available when generation fails; this
endpoint does not publish externally.

## Errors and retention

Errors have `{"error":{"code":"...","message":"...","details":null}}`.
Validation uses HTTP 422; missing/expired records 404; invalid selection 400;
capacity 429; unconfigured/unavailable Cala 503; invalid upstream responses 502;
resolution timeout 504. Invalid Cala configuration never silently returns demo data.

Runs expire after the configured retention interval. This MVP has no accounts,
CRM integration, email sending or contact enrichment. The Next.js app consumes
this API; all ranking remains in the backend.
