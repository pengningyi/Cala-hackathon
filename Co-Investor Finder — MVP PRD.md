# Co-Investor Finder — MVP PRD

Nov 14, 2026 · @Maria

## Overview

A founder who already has a lead investor enters their round details and gets, in under two minutes, a ranked shortlist of 5–10 co-investors plus a list of investors to avoid, every claim backed by a Cala source.

**Problem.** Once a lead commits, the founder still has to fill the rest of the round. Today they build the list by hand from LinkedIn, Crunchbase tabs and gut feeling. They pitch funds that no longer deploy, funds that only lead, or funds that already backed a competitor.

**Target user.** A seed or Series A founder in Europe with a signed or verbal commitment from a lead VC and an open allocation of roughly 20–50% of the round.

**Job to be done.** "My lead is in. Tell me who is most likely to join them, why, and who I should not send my deck to."

**Why Cala.** The core signal is relational: which investors have repeatedly co-invested with *this* lead, recently, at *this* stage. That is a graph traversal over investors, rounds and companies, with sources on every edge. Web search returns articles, not that graph.

## Design principles

1. **Lead-anchored.** Every search starts from the lead's co-investment graph, not from a generic sector filter. This is our angle and our Cala story.
2. **Evidence over opinion.** Every reason and every flag links to a Cala source and date. A claim without a source is not shown.
3. **Say "unknown", never guess.** If Cala has no data for a field (check size, last activity), the card shows "unknown". The LLM never fills gaps from its own memory.
4. **Flags are as valuable as picks.** The "do not pitch" list saves the founder weeks and protects their information.
5. **Show the work.** A live agent trace shows which Cala queries ran. This builds trust and makes Cala visibly core.
6. **One job, two minutes.** One form in, one shortlist out. No accounts, no CRM, no email sending in the MVP.

## User flow

The founder touches the product three times: fill the form, confirm the matched entities, read the result.

1. **Fill the form.** Company, lead investor, amounts and a few preferences (see Features).
2. **Confirm entities.** The app shows what it matched in Cala: "Your company = X (Helsinki, founded 2023). Your lead = Y (Fund, Finland)." The founder confirms or picks the right one. A wrong match here breaks everything downstream.
3. **Watch the agent run.** A progress panel streams each step and Cala query, about 30–90 seconds.
4. **Read the result.** A ranked shortlist of 5–10 investors and a separate flagged list, each with reasons and sources.
5. **Act.** Copy a short intro request to the lead ("Could you introduce us to Z? You co-invested in A and B"), or export the list as CSV.

## Features

P0 is the MVP that must work end to end by the demo. P1 is added only if P0 is done by Sunday morning.

### Input form

| Field | Required | Why we need it |
| --- | --- | --- |
| Founder name | Yes | Personalises the intro draft |
| Company name | Yes | Resolve the company in Cala |
| Company website | Yes | Disambiguates companies with the same name |
| One-line description / sector | Yes | Sector fit and competitor detection |
| Country (HQ) | Yes | Geography fit |
| Round stage (pre-seed, seed, Series A) | Yes | Stage fit |
| Round size target (€) | Yes | Computes the open allocation |
| Already committed (€), including the lead | Yes | Open allocation = target − committed |
| Lead investor name | Yes | Anchor of the whole search |
| Lead's ticket (€) | Optional | Context for the intro draft |
| Investors already in the round / cap table | Optional | Excluded from results, not flagged |
| Known competitors | Optional | Improves conflict detection |
| Preferences: include angels, include corporate VCs, geography | Optional | Filters |

### Feature list

| ID | Feature | Priority |
| --- | --- | --- |
| F1 | Input form with validation (fields above) | P0 |
| F2 | Entity resolution: match company and lead to Cala entities, founder confirms | P0 |
| F3 | Co-investor discovery from the lead's past rounds (who joined, how often, when) | P0 |
| F4 | Sector and stage candidates: investors active in similar companies, to add beyond the lead's network | P0 |
| F5 | Investor profile per candidate: last activity, stage mix, lead vs follow ratio, typical ticket | P0 |
| F6 | Flagging engine: conflict, inactive, wrong stage, lead-only, ticket mismatch (see Scoring) | P0 |
| F7 | Ranking with a short reason per investor, every reason with a source link | P0 |
| F8 | Result page: shortlist cards and a flagged list | P0 |
| F9 | Live agent trace showing steps and Cala queries | P0 |
| F10 | Intro request draft to the lead, per investor | P1 |
| F11 | CSV export | P1 |
| F12 | Allocation plan: suggested mix of tickets that fills the open amount | P1 |
| F13 | Warm path: people links (partner X worked with your lead's partner) | P1 |
| — | Accounts, saved searches, CRM sync, sending emails, investor contact data | Out of scope |

## Agent system

Five steps in a fixed order, run by one orchestrator. Three agents call Cala; scoring is deterministic code; the last agent only turns computed facts into sentences.

&#91;embedded content: agent pipeline · 3 Cala-calling agents, 1 code step, 1 writer\]

The Graph Explorer is the core: it walks from the lead to its past rounds and collects everyone who joined them. After step 1 the run pauses until the founder confirms the matched company and lead.

| Step | Input | Cala calls | Output |
| --- | --- | --- | --- |
| 1 Resolver | Form fields | Entity search for company and lead | Cala IDs, inferred sector, likely competitors |
| 2 Graph Explorer | Lead ID, sector, stage | Lead's rounds → participants; recent rounds in the sector at this stage | 20–50 candidate investors with shared-deal evidence |
| 3 Profiler | Candidate IDs | Each candidate's rounds in the last 24 months | Activity, stage mix, lead vs follow ratio, ticket, portfolio companies |
| 4 Score + flags | Profiles, form, competitors | None | Scores, flags, shortlist and flagged list |
| 5 Writer | Computed facts + source URLs | None | One-line reason per investor, intro draft (P1) |

**MVP shortcut.** The orchestrator can be one LLM with Cala as a tool and a fixed step prompt, as long as each step's output is logged to the trace. Splitting into separate agents is optional.

**Stack suggestion.** Next.js form and result page, Python (FastAPI) backend, Cala via API or MCP, an LLM for steps 1, 2 and 5, server-sent events for the live trace.

## Scoring and flagging

Scoring is plain code, not the LLM. That keeps results stable between runs. The LLM only writes the one-line reason from the computed facts. Weights are a first guess, to be tuned on the demo case.

### Match score (0–100)

| Signal | Weight | How it is computed |
| --- | --- | --- |
| Co-investment with the lead | 35 | Number of rounds shared with the lead, extra weight for the last 24 months |
| Recent activity | 20 | Rounds in the last 12 months; 0 if none |
| Stage fit | 15 | Share of their rounds at the founder's stage |
| Sector fit | 15 | Share of their portfolio in the same or a neighbouring sector |
| Follow behaviour | 10 | Share of rounds where they joined but did not lead |
| Geography fit | 5 | Invested in the founder's country or region before |

### Flags

A flagged investor is removed from the shortlist and shown in the flagged list with its reason and source.

| Flag | Rule | Severity |
| --- | --- | --- |
| Conflict | Backed a company the founder listed as a competitor, or one in the same narrow sector at a similar stage | Red: do not pitch |
| Inactive | No round found in the last 12 months | Grey: likely not deploying |
| Lead-only | Leads more than 80% of the rounds they join | Grey: unlikely to follow |
| Stage mismatch | Fewer than 10% of their rounds at the founder's stage | Grey |
| Ticket mismatch | Typical ticket larger than the open allocation | Amber: may not fit |
| Already in | Listed by the founder as already committed | Excluded silently |

Thresholds (80%, 10%, 12 months) are adjustable constants in code.

## Output

One result page with two lists: a shortlist to pitch and a flagged list to skip.

### Shortlist card (5–10 investors, sorted by score)

- **Name, type, country** (e.g. VC fund, Finland)
- **Match score** and a one-line reason, e.g. "Joined 3 of your lead's rounds since 2024, last in March 2026"
- **Shared deals with the lead**: up to 3 company names with round and date
- **Activity**: rounds in the last 12 months, last round date
- **Fit**: stage mix, typical ticket (or "unknown"), sector overlap
- **Sources**: links behind every fact above
- **Action** (P1): "Draft intro request to lead"

### Flagged list

- **Name**, one-line reason, source link
- Example: "Conflict — led the seed round of CompetitorCo (June 2025)"

### Header summary

- Open allocation: target − committed, e.g. "€\[XX\] still to fill"
- Counts: candidates scanned, shortlisted, flagged

## Success criteria, demo and build plan

### Success criteria for the demo

- Form to result in under 90 seconds
- 100% of shown reasons and flags carry a source link
- test case of company "Cala". On our test case, at least top 5 investors are confirmed plausible by a quick manual check
- At least one real conflict flag in the demo

### 
