# Cala tool mapping

The official [Cala MCP documentation](https://docs.cala.ai/integrations/mcp)
specifies `https://api.cala.ai/mcp/` and `X-API-KEY` authentication. Set
`CALA_API_KEY` in `backend/.env`; `CALA_MCP_URL` can override the endpoint. An
optional `CALA_MCP_TOKEN` supplies Bearer authentication for a custom proxy. Use
`GET /api/integrations/cala/tools` to inspect the real `tools/list` output.
The default native adapter has been verified against live Cala funding-event
schemas. It discovers fields and joins exact event UUIDs without a mapping file.
This document describes the optional mapped adapter for another reviewed response
contract; leave `CALA_TOOL_MAPPING` unset for the native integration.

Set `CALA_TOOL_MAPPING` to the absolute path of a JSON mapping with three keys:
`search_entities`, `lead_graph`, `investor_profile`. Each operation contains:

- `tool`: exact advertised Cala tool name.
- `arguments`: object matching that tool's advertised `inputSchema`. Exact
  strings `{query}`, `{kind}`, `{lead_id}`, `{investor_id}`, `{lookback_months}`
  substitute typed operation variables; other values are literal.
- `response_path`: optional JSON pointer into the tool's structured JSON output.
- `projection`: optional deterministic projection into the normalized model.
  `{"$path":"/field"}` copies a returned field;
  `{"$each":"/items","template":{...}}` maps array elements;
  `{"$retrieved_at":true}` records this actual retrieval time.

The search operation returns an `Entity[]`, graph returns `GraphSnapshot`, profile
returns `InvestorProfile` (see `app/models.py` and `/openapi.json`). Responses may
use MCP `structuredContent` or JSON text content. Arguments are checked against
the advertised tool schema; normalized outputs are checked with Pydantic.

Review mappings against actual responses. Never set `complete`,
`activity_window_complete`, investor roles, identities, or public facts as literal
constants unless the endpoint's contract explicitly establishes them. Missing
fields stay null/unknown; source URLs must come from Cala. Source retrieval times
are stamped on fresh responses and retained by the cache. Funding-event dates and
source publication dates remain separate. Do not infer an investor ticket from a
funding-event total.

The optional mapped adapter supports one tool call per normalized operation. If a custom Cala
API needs graph joins over several tools, add a reviewed adapter for that schema
and charge each underlying tool call against the run budget before integration
readiness is claimed. `tools/list` metadata is cached per provider and is reported
separately from data tool-call counts.

Cala advertises `entity_search`, `entity_introspection`, `entity_retrieval`,
`knowledge_query` and `knowledge_search`. The graph is dynamically typed; the
documented tool names alone do not establish funding-event property names or
normalized output projections. Inspect the actual tool schemas and a bounded
authenticated response before configuring discovery and profiling. A configuration
template is not evidence that the live Cala pipeline or readiness gate passed.
