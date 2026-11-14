# Backend

Requires Python 3.11+. From this directory on Windows:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On macOS/Linux use `.venv/bin/python` instead. Interactive API documentation is at
<http://127.0.0.1:8000/docs>. `GET /api/health` reports whether Cala configuration
is present; it does not claim successful authentication or live data readiness.

Set `CALA_API_KEY` in `.env`; the default endpoint is `https://api.cala.ai/mcp/`
and its documented key header is `X-API-KEY` ([Cala MCP documentation](https://docs.cala.ai/integrations/mcp)).
`CALA_MCP_URL` and `CALA_TRANSPORT` can override the transport; `CALA_MCP_TOKEN`
is optional bearer authentication for a custom proxy.
The default native adapter discovers the server's schemas, then joins investor
participations through exact funding-event UUIDs. `GET /api/integrations/cala/tools`
exposes read-only schema discovery. `CALA_TOOL_MAPPING` is an optional override
for another reviewed response contract; see [mapping instructions](config/README.md).
Never commit `.env` or API keys.

The default `MODEL_PROVIDER=codex` uses the installed Codex CLI and your existing
login. `CODEX_EXECUTABLE` can be an absolute path if Codex is absent from PATH.
It runs ephemeral, read-only structured model requests with external tools
disabled. It receives the founder profile and retrieved Cala facts; Cala
credentials are excluded from its subprocess environment. A slow/unavailable model
leaves semantic fit unknown and uses deterministic introduction templates.
Live chat uses the same provider with a separate generation request for each
question, including recent conversation history, the selected investor and a
bounded snapshot of the saved result. Replies can explain scores, relationships,
allocation and next steps or help draft an introduction. They do not change the
run or perform new discovery. Returned citations resolve only to saved sources.
Chat failures produce an explicit retryable error rather than a canned answer.
`CHAT_TIMEOUT_SECONDS=60` and `CHAT_MAX_CONCURRENCY=2` control chat independently
of the discovery assessment budget.

To use your own foundation model instead:

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://your-provider.example/v1
MODEL_API_KEY=your-private-key
MODEL_NAME=your-model
```

The provider must support `/chat/completions` and JSON-schema structured output.
Native APIs using another protocol need a `SemanticProvider` adapter. No specific
model is imposed. `MODEL_PROVIDER=disabled` keeps semantic assessments unknown
and makes live chat unavailable until a provider is enabled.
The model never sets scores, weights, entity identities or exclusion rules.
The current writer uses sourced templates for every completed card. Semantic
overlap assessment uses the selected model when it completes in budget; main's
sector score uses its deterministic keyword rule rather than the model's rating.
The local CLI structured-output smoke test passed with network access. Verify
login, network access and model latency on the machine running the service.

On `branch-basic`, setup starts with only the founder company and lead investor
names. `/api/onboarding/matches` searches both identities without requiring a
round profile. The founder explicitly selects and confirms both matches through
`/api/onboarding/matches/{id}/confirm`. Confirmation returns a token, the two
entities, editable company website/sector/HQ prefills and their source references.
Unknowns stay empty. Explicit Cala industry relationships can supply broad sector
labels. Matching retains the owner's REST knowledge query, followed by bounded
MCP introspection and retrieval for full available metadata. Entity country uses
headquarters, then office address, then registration when no office exists;
fallbacks retain their own sources and basis. The owner's headquarters form field
is prefilled only from HQ evidence. Portfolio Country-match scoring
receives headquarters-only facts, preserving the owner's rule semantics.
Descriptions do not substitute for sourced field facts.

The founder then enters the missing fields, current stage, target and commitments
and submits the completed profile to `/api/runs` with the confirmation token.
Neither historical stage nor round totals prefill current fundraising facts.
The selected identities are enforced server-side; other profile fields remain
editable. Lead commitment starts as `unconfirmed`. Matching and confirmation do
not start funding discovery or model assessment. The older `/api/resolve` contract
remains available for existing full-profile API clients.

The owner's scoring and ranking use `app/rules.py`, `app/ranker.py` and
`app/scoring.py` from `df78081`: 12 binary rules, one point per rule met, with
unchecked facts shown separately. `RunResult.scoring_version` is `rules-2026-11`.
Founder-network discovery, trust loops, rule explanations and the action ladder
are retained. `app/main_scoring.py` remains a legacy/helper module. Allocation
percentages use the owner's greedy method with labelled equal-share estimates; they
are neither actual investor checks nor probabilities. Only known EUR totals
enter those estimates. See [integration details](../docs/branch-basic-integration.md).

Run verification from this directory:

```powershell
.venv/Scripts/python -m pytest -q
```

Automated pytest cases use explicitly synthetic providers. They do not spend model API credits
or prove that the real Cala service works. The live workflow CLI uses a real
server and accepts a JSON `ResolveRequest` (see [API contract](../docs/api.md)):

```powershell
.venv/Scripts/python scripts/run_profile.py --profile my-profile.json
.venv/Scripts/python scripts/run_profile.py --profile one.json --profile two.json --profile three.json --runs 10 --report reports/live.json
```

The CLI records resolver latency separately, processing latency, request counts,
cache hits, completeness and draft availability. It refuses ambiguous lead matches
unless `--lead-id` identifies the founder's chosen result. Running multiple profiles
with different resolved IDs can select each lead interactively. `--lead-id` is
useful for a single already-reviewed lead. Review each lead in `/docs` before
accepting its ID.

Runs and confirmation records are held in memory for one hour by default; restart
loses them. Real Cala response cache files live in `.cache/` and retain original
retrieval timestamps. Both cache identity and TTL are configurable. Run with **one
worker** so confirmation records and SSE replay share the same process. The initial
service is intended for local development. The frontend consumes its HTTP/SSE API;
public deployment is a separate step. The supplied deployment skill describes another
repository, so its passwords, GPU requirements and application routes do not apply.
