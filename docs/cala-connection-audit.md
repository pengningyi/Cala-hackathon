# Cala API: investor connection audit

Checked against the live configured MCP endpoint on 14 November 2026. This was a
read-only investigation: 23 successful data-tool calls, plus explicit tool
discovery. Production discovery, scoring and UI behavior were not changed.
Credentials remained in the ignored backend environment file. Raw responses,
arguments and retrieval timestamps are in ignored
`backend/.runtime/connection-audit-*.json` files.

## Main finding

Cala has useful company, funding-event, founder, employment, board and membership
edges. RoundMe currently uses only a small part of this graph. We can improve
the existing lead-connected shortlist through better funding-event discovery, and
add clearly labelled founder-network paths as a separate expansion.

An observed institutional or historical affiliation is evidence to investigate an
introduction. It does not establish personal closeness, willingness to introduce,
current investment appetite or a commitment to the founder's current round.

## Tools and tested behavior

The live MCP server advertised six tools. Five data tools were exercised; external
identifier lookup was inspected in the tool schema but not called.

| Tool | Use for RoundMe | What this audit established |
| --- | --- | --- |
| `entity_search` | Resolve company, investor and person identities | Altimeter search returned multiple legal entities and vehicles, requiring identity confirmation |
| `entity_introspection` | Discover fields and edge types before retrieval | Available edges differ substantially across Cala, Lightspeed, Altimeter and individual people |
| `entity_retrieval` | Retrieve explicit incoming/outgoing graph edges and sources | Founder, investor, board, employment and role-bearing membership paths are populated; relationship pagination works |
| `knowledge_query` | Find relevant companies or funding events by stage and sector | Useful discovery names, but the tested response omitted funding-event IDs and included inconsistent company IDs |
| `knowledge_search` | Sourced context and narrowly targeted questions | A Cala-specific partner search returned sources but identified no named Lightspeed deal partner in its retrieved evidence |
| `entity_lookup` | Resolve an external identifier such as LEI or CIK | Advertised; not needed or tested for these connection paths |

Default retrieval returns properties. Relationships require a projection, and
available fields should first be discovered through introspection. Per-edge
`limit` and `offset` are documented and worked in the live test. The documented
projection does not promise arbitrary edge filtering or chronological ordering.
See [MCP tools](https://docs.cala.ai/integrations/mcp),
[introspection](https://docs.cala.ai/api-reference/entity-introspection), and
[entity retrieval](https://docs.cala.ai/api-reference/entities).

There is no dedicated shortest-path, willingness-to-introduce or personal
relationship-strength tool in the returned MCP tool list. Paths must be assembled
from retrieved edges and their provenance.

## Concrete paths returned by Cala

### Existing investor network

For Cala AI, the returned website was `cala.ai` and the entity ID was
`81cfdabc-53f7-426a-b358-a942e159ca0f`. Incoming `INVESTED_IN` edges identify
Lightspeed Venture Partners, Kfund, Kibo Ventures and Masia VC. These are starting
points for exploring existing investor networks. They are not newly discovered
prospects or evidence of commitments to the hypothetical round in the test form.

The API also returned founders Elisenda Bou-Balust and Issey Masuda Mora through
incoming `FOUNDED`, with further executive relationships. Its public
`funding_stage` value was `PRE_SEED`; the form's seed round and amounts remain
founder-supplied hypothetical test inputs.

Evidence: `connection-audit-cala-relations.json`. Returned sources include the
[founder announcement](https://www.linkedin.com/posts/elisendabou_were-finally-open-14-months-ago-activity-7459601462898266112-NJCs)
and [Cala jobs page](https://cala.ai/jobs), with dates attached to the specific
edges. Public past investment must remain separate from current-round status.

### A relevant identified shared round beyond the first page

The first and second pages of Lightspeed's outgoing
`PARTICIPATES_IN_FUNDING_ROUND` each returned 20 distinct event IDs with no overlap.
The combined 40 included 8 labelled Seed events and 14 labelled Series A events.
These labels establish discovery context; exact dates, issuers and participants
still require event retrieval. None of those 40 edge projections carried an
`event_date`, so source publication dates cannot stand in for event dates.

Exa's Series A was on the second page (`offset: 20`):

`Lightspeed -> identified Exa Series A <- NVentures / Y Combinator`

Exact event `8b1739cf-678b-59f9-9925-de3e14acd486` returned Exa as `ISSUER`,
Lightspeed as `INVESTOR` with explicit `is_lead: true`, and NVentures, Y Combinator
and Initialized Capital as investor participants. Sources differed across edges;
the [company announcement](https://exa.ai/blog/series-a) supports the Lightspeed,
NVentures and Y Combinator edges. Initialized's edge cites an aggregator, so its
provenance should remain separately visible.

This is a closer stage/sector discovery example than a Series H syndicate. It
does not by itself establish current seed appetite or an introduction, and Exa's
product context should also inform portfolio-overlap considerations.

Evidence: `connection-audit-lightspeed-relations.json`,
`connection-audit-lightspeed-page2.json`, `connection-audit-exa-relations.json`,
`connection-audit-exa-series-a-intro.json`, and
`connection-audit-exa-series-a-relations.json`.

### Founder and previous-company network

The following explicit edges were retrieved:

`Cala AI <- FOUNDED - Elisenda -> FOUNDED - Vilynx <- INVESTED_IN - historical investors`

Vilynx's investors included Kibo Ventures, Caixa Capital Risc, Ridgewood Capital,
Joe Golden and Ramon Bernat Serra. These are historical founder-network leads;
they do not satisfy the MVP's same-round-with-selected-lead shortlist rule.
Some investor edges cite aggregators; the angel edges include
[2017 funding coverage](https://www.webcapitalriesgo.com/240217_6_10300/).

A more specific person path was also present:

`Elisenda -> founded Vilynx <- board member Sonia Fernandez -> works at Kibo Ventures`

The Vilynx board edge cites a
[2017 SEC filing](https://www.sec.gov/Archives/edgar/data/1532213/000153221317000001/0001532213-17-000001.txt)
and has `valid_since: 2017-02-01`. Sonia's Kibo affiliation cites a
[secondary fund directory](https://venturecapitalarchive.com/venture-funds/kibo-ventures-kiboventures-com),
with no explicit title or end date on the employment edge. It establishes an
observed affiliation to confirm, not a verified current title or personal access.

Elisenda also has an `IS_MEMBER_OF` edge to Masia with role
`Advisory Board Member`, including a
[Masia profile source](https://www.masia.vc/collective-members/elisenda).
The Masia membership entity and Cala's investor entity named Masia VC have
different IDs; do not merge them solely by name.

Evidence: `connection-audit-elisenda-relations.json`,
`connection-audit-issey-relations.json`, `connection-audit-vilynx-relations.json`,
and `connection-audit-sonia-relations.json`.

### Named partner and board context

Lightspeed's incoming `WORKS_AT` and role-bearing `IS_MEMBER_OF` edges returned
Paul Murphy. His profile exposed employment, investments, founded companies and
membership roles. One historical Northzone employment edge explicitly ended in
September 2021; another membership edge lacked an end date. Those observations
must retain their individual dates and cannot establish concurrent employment.

Exa's incoming `IS_BOARD_MEMBER_OF` edges returned named board members, including
Guru Chahal and Peter Fenton, with company/filing sources. A board edge alone does
not identify a person's fund or establish a founder's relationship with them;
those next-hop edges require independent retrieval.

The narrowly targeted `knowledge_search` did not identify a named Lightspeed
partner connected specifically to Cala's pre-seed in its retrieved evidence.
Therefore Paul Murphy must not be presented as Cala's deal partner just because
he appears on the fund's team. Absence from this bounded search is not proof that
no such public attribution exists elsewhere.

Evidence: `connection-audit-lightspeed-relations.json`,
`connection-audit-paul-murphy-relations.json`,
`connection-audit-exa-relations.json`, and
`connection-audit-cala-partner-search.json`.

## Discovery query limits

This natural-language `knowledge_query` was tested:

> Find up to 5 Seed or Series A funding rounds involving Lightspeed Venture
> Partners and AI infrastructure companies since 2021. Return company name,
> round date, stage, co-investors, and available entity IDs for the funding round
> and participating investors.

It returned discovery leads including Inferact, Composio and Exa, but no exact
funding-event IDs. Some company IDs were null, and some differed between the
result rows, the accompanying `entities` list and graph retrieval. Its investor
names must not be used as already-verified same-event relationships. Resolve and
validate candidate identities, then retrieve the identified event and both
investor participations before adding a main-shortlist path.

The query took 50.328 seconds; the targeted sourced search took 38.421 seconds.
These are single observations, not latency benchmarks. They consume a substantial
part of the MVP's 90-second deadline. Prefer bounded typed graph retrieval for the
core route; make broader discovery queries optional, cached or time-bounded with
a useful typed-retrieval fallback. Query syntax supports natural language and dot
notation, but the output schema is query-dependent. See
[structured query](https://docs.cala.ai/api-reference/query) and
[sourced search](https://docs.cala.ai/api-reference/search).

## Recommended implementation order

1. **Improve the existing lead graph.** Page funding relationships within explicit
   call limits and prioritize observed stage/context before expanding exact
   events. Preserve unknown event dates until retrieved. The current provider
   sorts a bounded projection and expands three events; this is not a guarantee
   that they are the lead's three latest events overall.
2. **Preserve the existing shortlist evidence rule.** Admit a co-investor only
   after validating the same identified funding event, issuer, dates, investor
   roles and per-edge sources. Keep historical mismatch advisory, unless a
   published applicable mandate or an explicit founder filter excludes it.
3. **Add founder-network paths separately.** Use a founder-confirmed startup to
   resolve founders, previous companies, historical backers, board members and
   fund affiliations. Keep this broader path type distinct from exact-round
   co-investors. This extends the PRD's P0 discovery scope.
4. **Add person details where observed.** Retrieve only advertised relationships
   and retain role, `valid_since`, `valid_until`, sources and retrieval timestamp
   for every hop. Ask the founder to confirm who they know and who can introduce
   them; do not infer personal access from a shared organization or event.
5. **Separate route evidence and investment fit in the UI.** Show why a path
   exists alongside stage, product relevance, geography, participation-size
   evidence and unknowns. No graph path represents a probability of investment.

Do not silently merge similarly named companies, funds or people. Unsourced
descriptions, missing role dates, generic source links and inconsistent query IDs
are present in the sampled responses. Preserve those limits in the stored graph
and display only claims supported by the particular edge being explained.
