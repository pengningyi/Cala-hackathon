# Roundfill — find co-investors once your lead is in

A founder enters their round and lead investor. Agents walk Cala's knowledge graph from the lead
to its past rounds to the investors who joined them, then score, flag and explain each candidate.
Default test case: **Cala AI**, seed round, lead auto-detected from Cala.

## Run it (5 minutes)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your CALA_API_KEY (and optionally ANTHROPIC_API_KEY)
python ../scripts/probe.py "Cala AI"     # FIRST: check the raw shapes Cala returns
uvicorn main:app --reload --port 8000
# open http://localhost:8000  -> pill shows "CALA LIVE"
```

Without a key, or when the page is opened on its own, it runs in **demo mode** with fictional
sample investors (clearly labelled), so the UI can be shown offline.

## Architecture

```
Browser (static/index.html)
  │  POST /api/resolve  ──────────────▶  FastAPI (main.py)
  │  POST /api/run  ◀── SSE stream ───   │  pipeline.py  (agents, orchestrated in a fixed order)
  │                                      │  scoring.py   (deterministic score + flags)
  │                                      │  writer.py    (template, or LLM wording if ANTHROPIC_API_KEY)
  │                                      └─ cala.py      (httpx client: cache.json, 4 concurrent, retry 429)
  │                                                │
  │                                                ▼
  │                                     Cala API  https://api.cala.ai  (header X-API-KEY)
```

One event stream drives the whole UI: `step`, `call`, `node`, `edge`, `result`, `error`.
The graph, the agent trace and the result tables are all drawn from it.

## Agent workflow

| # | Agent | Cala endpoint | Input (example for Cala AI) | Output |
|---|---|---|---|---|
| 1 | Resolver | `GET /v1/entities?name=…&entity_types=Company` | `Cala AI` | entity candidates; founder confirms (there are several "Cala"s) |
| 1b | Lead auto-detect | `POST /v1/knowledge/query` | `funding_rounds.company=Cala AI.order_by=date DESC.limit=3.return(company, round_type, date, amount, lead_investor, investors)` | lead name, then `GET /v1/entities` to match it |
| 2 | Graph Explorer | `POST /v1/knowledge/query` | `funding_rounds.investors={lead}.date>={24 months ago}.order_by=date DESC.limit=40.return(...)` | lead's deals → co-investors, with shared-deal counts |
| 2b | Fallback | `GET /v1/entities/{id}/introspection` + `POST /v1/entities/{id}` | relationships whose name contains INVEST / FUND / ROUND / CORPORATE_EVENT | companies the lead is linked to, then their rounds |
| 3 | Sector Scout | `POST /v1/knowledge/query` | `funding_rounds.company.industry={sector}.round_type={stage}.date>=….return(...)` | extra candidates outside the lead's network |
| 3b | Competitors | `POST /v1/knowledge/query` | `companies.competitors_of={company}.limit=10.return(name)` (or the form field) | list used by the conflict flag |
| 4 | Profiler | `POST /v1/knowledge/query` ×N | `funding_rounds.investors={candidate}.date>=….return(...)` | rounds, stage mix, lead ratio, ticket, portfolio |
| 5 | Scorer | none (code) | profiles + form | score 0–100, flags, shortlist / flagged |
| 6 | Writer | `POST /v1/knowledge/search` | `Who invested in {shared deal}'s {stage} round?` | source URLs (KnowBit origins), reason, intro draft |

**Score** = 35 co-investment with lead + 20 recent activity + 15 stage fit + 15 sector fit + 10 follows (not only leads) + 5 geography.
**Flags**: conflict (backed a competitor, red) · inactive >12 months · lead-only >80% · stage <10% · ticket > open allocation (amber, stays on the list).
Thresholds are constants at the top of `scoring.py`; the same rules are mirrored in `index.html` for demo mode.

## Things to check with your key first

1. Run `scripts/probe.py`. `knowledge_query` rows have a query-dependent schema. If the keys differ from
   `company / investors / lead_investor / round_type / date / amount / industry / country`, add the new
   spellings to `norm_round()` in `pipeline.py`.
2. If `funding_rounds.investors=X` returns nothing, try the natural-language form
   (`"Which funding rounds did X participate in since 2024?"`) — same endpoint, same parser.
3. Keep `cache.json`: a successful demo run is replayed from cache at zero credits and works if the Wi-Fi dies.

## Security

Keep the key only in `backend/.env` (git-ignored). Never put it in `index.html`: the browser talks only to
your backend. Rotate the key after the hackathon.
