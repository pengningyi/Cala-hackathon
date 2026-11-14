# Backend readiness gate

Current status: **technical backend gate passed on 2026-11-14, before frontend implementation**.

The gate passed with 67 automated tests and ten founder-confirmed runs against
real Cala MCP data. The tests cover manual startup profiles, entity confirmation,
same-event joins, deterministic scoring, investor statuses, exclusions, unknown
fields, no open allocation, no match, deadlines, partial results, call budgets,
concurrency, retries, caching, HTTP responses and replayable SSE progress. All 67
tests passed again after the model timeout and optional startup-confirmation
changes. Synthetic fixtures establish policy behavior; the separate live batch
establishes Cala integration.

The live flow used the HTTP API to resolve a lead, select an offered Cala ID,
confirm the manual startup profile, start a run, retrieve structured results and
consume its progress stream. Credentials remain in the ignored backend `.env`.
The native adapter uses Cala's discovered schemas and source-backed entity/event
relationships. Each underlying data-tool attempt, including retries and joins,
counts toward the 20-call budget; raw cache hits retain retrieval timestamps.

The ten-run batch used three explicitly hypothetical founder profiles: Cala's
AI infrastructure seed example in Spain with Lightspeed, an Arcane marketing
seed example in the UK with Seedcamp, and the fictional Nordic Grid energy
Series A example in Finland with Lifeline Ventures. Every lead was conditional.
The startup descriptions, round amounts and investor commitments are demo inputs,
not claims about those companies' actual financing or investor interest.

The original benchmark used an **8-second model cap**. Its measurements remain
unchanged in the generated, gitignored
[backend acceptance report](../backend/reports/backend-acceptance.json).
There were three cold runs with isolated scenario caches and seven warm runs.
Percentiles use linear interpolation at `p * (n - 1)`.

| Measurement | All ten runs | Cold: three runs | Warm: seven runs |
| --- | --- | --- | --- |
| Median processing | 8.352 s | 17.938 s | 8.157 s |
| p95 processing | 26.626 s | 32.154 s | 8.386 s |
| Maximum processing | 33.734 s | 33.734 s | 8.391 s |
| Median resolver latency | 15.5 ms | 2,578 ms | 15 ms |
| p95 resolver latency | 3,764.35 ms | 4,519.3 ms | 16 ms |
| Actual Cala calls per confirmed run | 0–19 | 17–19 | 0 |
| Raw-response cache hits per confirmed run | 0–19 | 0 | 17–19 |

All ten runs passed the shared-event identity, source/date, confirmation,
normalized-score, draft, deadline, budget and SSE assertions. Each returned four
or five cards: 47 cards across the batch, with a copyable draft on every card.
Median profile completeness was 70%, ranging from 35% to 70%. Technical usable
result and draft-availability rates were both 100%; these rates describe valid
outputs, not founder endorsement or investment likelihood. The short original
model cap produced no semantic assessments; seven runs explicitly reported model
fallback. Missing semantic fit stayed unknown.

A supplemental **whole API run with the current 20-second Codex cap** used the
Cala scenario and its real warm response cache. It completed in **17.938 seconds**,
with five cards, five labelled semantic assessments, zero new Cala data calls and
19 raw cache hits. Every candidate had an assessment. Each assessment's company
ID and source links were checked against that candidate's retrieved portfolio
facts; no model-failure notice appeared. All original acceptance assertions and
the added semantic provenance checks passed. The result and SSE trace are under
`supplemental_runs` in the same report and are excluded from the original ten-run
statistics. This demonstrates a successful model-assisted pipeline within the
90-second global deadline; the sourced template fallback remains available.

Coverage is deliberately bounded. The native graph adapter inspects at most
three lead funding events within the 60-month lookback and does not claim
exhaustive coverage. All measured results therefore have `state: partial`, even
when every selected candidate profile finished. Shared-event and company counts
describe observed evidence. Candidate activity and historical participation also
use independently retrieved investor observations in the 24-month profile
window, rather than treating the lead's shared events as the candidate's full
investment history. Unknown stages, roles, dates and activity coverage remain
unknown; observations do not establish an exhaustive investment record.

Introduction and follow-up requests currently use deterministic, source-backed
templates. Codex supplies labelled sector/overlap assessments; numeric ranking and
exclusion decisions remain in code. There is no automatic email sending, ticket
guarantee or inferred personal introduction relationship.

Founder feedback identifying an investor and request they would act on has **not
been collected**. It remains a separate product acceptance item, alongside the
integrated browser checks after frontend implementation. The technical backend
gate authorizes that next implementation phase without claiming those later
checks are complete.

From `backend/`, the verification entry points are:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/acceptance_live.py
.\.venv\Scripts\python.exe scripts/acceptance_semantic.py
```

The live runner records a new ten-run batch using the current configuration. The
semantic runner appends its measurement and verifies that the saved original
benchmark content is unchanged.
