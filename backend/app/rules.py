"""One rule set for scoring and ranking.

Each rule is met (1 point), not met (0) or not checked (no data; 0 points, shown
as such). The score is the number of rules met. Ranking uses the same rules:
most rules met, then most Connection rules met, then the most recent evidence.
Rules that Cala's data cannot support were removed rather than guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .models import CandidateCard, Entity, FundingRound, InvestmentObservation, ScoreComponent, Source


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    group: str
    met_when: str


RULES = [
    Rule("lead_coinvestor", "Lead co-investor", "Connection", "At least one shared round with your lead"),
    Rule("repeat_partner", "Repeat partner", "Connection", "Shared rounds with your lead in two or more companies"),
    Rule("recent_partner", "Recent partner", "Connection", "A shared round with your lead in the last 24 months"),
    Rule("your_investor", "Your investor", "Connection", "Invested in one of your company's own rounds"),
    Rule("backed_your_founder", "Backed your founder", "Connection", "Backed a company one of your founders previously founded"),
    Rule("warm_path", "Warm path", "Connection", "At least one trust loop links you to the investor"),
    Rule("lead_closed_path", "Lead-closed path", "Connection", "A trust loop that also runs through your lead"),
    Rule("recently_active", "Recently active", "Activity", "Invested in the last 12 months"),
    Rule("stage_match", "Stage match", "Fit", "At least 25% of its rounds with a known stage are at your stage"),
    Rule("sector_match", "Sector match", "Fit", "Backed a company in your sector"),
    Rule("country_match", "Country match", "Fit", "Backed a company headquartered in your country"),
    Rule("no_competitor", "No competitor", "Risk", "Has not backed a competitor you named"),
]
BY_ID = {rule.id: rule for rule in RULES}
CONNECTION = {rule.id for rule in RULES if rule.group == "Connection"}
STAGE_SHARE = 0.25
RECENT_PARTNER_MONTHS = 24
ACTIVE_DAYS = 365


@dataclass
class RuleContext:
    """What the run could check, so a missing lookup is 'not checked', never 'not met'."""
    startup_id: str | None = None
    startup_rounds_checked: bool = False
    previous_companies_checked: bool = False
    # Companies enriched with industry and headquarters (company id -> entity).
    companies: dict[str, Entity] = field(default_factory=dict)


def result(rule_id: str, status: bool | None, detail: str, sources: list[Source] | None = None) -> ScoreComponent:
    """met -> value 1; not met -> 0; None -> not checked (known=False)."""
    rule = BY_ID[rule_id]
    known = status is not None
    return ScoreComponent(name=rule.id, weight=1, value=1.0 if status else 0.0, known=known, group=rule.group,
                          explanation=detail if known else f"Not checked: {detail}",
                          sources=list(sources or []) if known else [])


def _terms(sector: str) -> list[str]:
    text = sector.lower()
    return [t.strip() for t in re.split(r"[,/;]| and ", text) + [text] if len(t.strip()) > 2]


def _industries(company: Entity | None, rounds: list[FundingRound]) -> tuple[list[str], list[Source]]:
    """Industry labels (from any round of the company or its own industry fact) with their sources."""
    labels, sources = [], []
    for funding in rounds:
        if funding.sector:
            labels.append(funding.sector)
            sources.extend(funding.sources)
    if company is not None:
        for item in company.metadata:
            if item.name in ("operates_in_industry", "sector", "industry", "industries") and item.sources:
                values = item.value if isinstance(item.value, list) else [item.value]
                found = [v for v in values if isinstance(v, str) and v.strip()]
                labels.extend(found)
                if found:
                    sources.extend(item.sources)
    return labels, sources


def _country(company: Entity, rounds: list[FundingRound]) -> tuple[str | None, list[Source]]:
    for funding in rounds:
        if funding.company_country:
            return funding.company_country, list(funding.sources)
    if company.country and company.country_sources:
        return company.country, list(company.country_sources)
    return None, []


def fit_rules(investor: Entity, founder_sector: str, founder_country: str, founder_stage: str,
              rounds: list[FundingRound], observations: list[InvestmentObservation],
              context: RuleContext, normalize_stage, normalize_country) -> list[ScoreComponent]:
    """Stage, sector and country rules over the investor's identified rounds."""
    out = []
    staged = [o for o in observations if o.stage]
    matching = [o for o in staged if normalize_stage(o.stage) == normalize_stage(founder_stage)]
    if staged:
        share = len(matching) / len(staged)
        out.append(result("stage_match", share >= STAGE_SHARE,
                          f"{len(matching)} of {len(staged)} rounds with a known stage are at {founder_stage.replace('_', ' ')}",
                          [s for o in staged for s in o.sources][:6]))
    else:
        out.append(result("stage_match", None, "no round with a known stage"))

    # Portfolio companies other than your own company, with any enriched facts.
    companies: dict[str, tuple[Entity, list[FundingRound]]] = {}
    for funding in sorted(rounds, key=lambda r: (-(r.event_date or date.min).toordinal(), r.id)):
        if funding.company.id != context.startup_id and any(p.investor.id == investor.id for p in funding.participants):
            entity, company_rounds = companies.setdefault(
                funding.company.id, (context.companies.get(funding.company.id, funding.company), []))
            company_rounds.append(funding)
    terms = _terms(founder_sector) if founder_sector else []
    labelled = {cid: _industries(entity, company_rounds) for cid, (entity, company_rounds) in companies.items()}
    labelled = {cid: found for cid, found in labelled.items() if found[0]}
    if not terms or not labelled:
        out.append(result("sector_match", None, "no industry found for its portfolio companies" if terms else "no sector given"))
    else:
        hits = [cid for cid, (labels, _) in labelled.items()
                if any(term in label.lower() or label.lower() in founder_sector.lower() for label in labels for term in terms)]
        names = ", ".join(companies[cid][0].name for cid in hits[:2])
        out.append(result("sector_match", bool(hits),
                          f"backed {names}" if hits else f"none of {len(labelled)} checked companies is in your sector",
                          [s for cid in (hits or labelled) for s in labelled[cid][1]][:6]))

    wanted = normalize_country(founder_country) if founder_country else ""
    located = {}
    for cid, (entity, company_rounds) in companies.items():
        country, sources = _country(entity, company_rounds)
        if country:
            located[cid] = (normalize_country(country), sources)
    if not wanted or not located:
        out.append(result("country_match", None, "no headquarters found for its portfolio companies" if wanted else "no country given"))
    else:
        hits = [cid for cid, (country, _) in located.items() if country == wanted]
        out.append(result("country_match", bool(hits),
                          f"backed {companies[hits[0]][0].name} in {founder_country}" if hits
                          else f"none of {len(located)} checked companies is in {founder_country}",
                          [s for cid in (hits or located) for s in located[cid][1]][:6]))
    return out


def trust_rules(card: CandidateCard) -> list[ScoreComponent]:
    if card.trust is None:
        return [result("warm_path", None, "no people or network data links you to this investor"),
                result("lead_closed_path", None, "no people or network data links you to this investor")]
    loops = card.loops
    closed = [loop for loop in loops if loop.closes_through_lead]
    sources = [s for loop in loops for hop in loop.hops for s in hop.sources]
    return [result("warm_path", bool(loops), f"{len(loops)} trust loop(s)" if loops else "no trust loop found", sources),
            result("lead_closed_path", bool(closed), f"{len(closed)} loop(s) also run through your lead" if closed
                   else "no loop runs through your lead", [s for loop in closed for hop in loop.hops for s in hop.sources])]


def apply(card: CandidateCard, extra: list[ScoreComponent]) -> CandidateCard:
    """Replace rule results by id, keep rule order, and recount the score."""
    by_id = {c.name: c for c in card.components}
    by_id.update({c.name: c for c in extra})
    card.components = [by_id[rule.id] for rule in RULES if rule.id in by_id]
    card.fit_score = sum(1 for c in card.components if c.known and c.value >= 1)
    card.profile_completeness = round(sum(1 for c in card.components if c.known) / len(RULES), 4)
    return card


def connection_met(card: CandidateCard) -> int:
    return sum(1 for c in card.components if c.name in CONNECTION and c.known and c.value >= 1)


def is_met(card: CandidateCard, rule_id: str) -> bool:
    return any(c.name == rule_id and c.known and c.value >= 1 for c in card.components)


def active_rule(dated: list[date], as_of: date, sources: list[Source]) -> ScoreComponent:
    if not dated:
        return result("recently_active", None, "no dated investment found")
    recent = [d for d in dated if (as_of - d).days <= ACTIVE_DAYS]
    latest = max(dated)
    return result("recently_active", bool(recent),
                  f"{len(recent)} investment(s) in the last 12 months" if recent else f"last dated investment {latest.isoformat()}",
                  sources)
