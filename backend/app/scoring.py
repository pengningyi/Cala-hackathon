"""Main-branch scoring over branch-one's typed Cala evidence and introduction drafts.

The numerical engine is ported from main commit 1fed306. Ticket estimates are
explicit equal-share heuristics, kept separate from factual investor criteria.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from . import main_scoring, ranker, rules as rule_book
from .entity_metadata import country_name
from .models import (
    AllocationItem, CandidateCard, Consideration, Entity, Exclusion, FounderInput, FundingRound,
    InvestmentObservation, InvestorFlag, InvestorProfile, InvestorStatus, ScoreComponent, Source,
)


def normalized_name(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


_COUNTRY_ALIASES = {
    "uk": "united kingdom", "gb": "united kingdom", "gbr": "united kingdom",
    "great britain": "united kingdom", "england": "united kingdom",
    "us": "united states", "usa": "united states", "united states of america": "united states",
    "ua": "ukraine", "fi": "finland", "fr": "france", "de": "germany",
    "se": "sweden", "dk": "denmark", "no": "norway", "nl": "netherlands",
    "be": "belgium", "ch": "switzerland", "at": "austria", "ie": "ireland",
    "ee": "estonia", "lv": "latvia", "lt": "lithuania", "pl": "poland",
    "pt": "portugal", "es": "spain", "it": "italy", "gr": "greece",
    "ro": "romania", "bg": "bulgaria", "cz": "czechia", "czech republic": "czechia",
    "sk": "slovakia", "hu": "hungary", "lu": "luxembourg", "is": "iceland",
    "si": "slovenia", "hr": "croatia", "cy": "cyprus", "mt": "malta",
    "ca": "canada", "au": "australia", "nz": "new zealand", "il": "israel",
    "sg": "singapore", "in": "india", "jp": "japan", "cn": "china",
}


def normalize_country(value: str | None) -> str:
    norm = normalized_name(value or "")
    norm = _COUNTRY_ALIASES.get(norm, norm)
    canonical = country_name(norm)
    return normalized_name(canonical) if canonical else norm




def normalize_stage(value: str | None) -> str:
    key = main_scoring.stage_key(value)
    return key.replace("-", "_")


def months_before(day: date, months: int) -> date:
    month_number = day.year * 12 + day.month - 1 - months
    year, month = divmod(month_number, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))




def unique_sources(sources: list[Source]) -> list[Source]:
    unique = {(str(source.url), source.retrieved_at.isoformat(), str(source.published_at)): source
              for source in sources}
    return [unique[key] for key in sorted(unique)]


def _sources(rounds: list[FundingRound] | list[InvestmentObservation]) -> list[Source]:
    return unique_sources([source for item in rounds for source in item.sources])


def _entity_names(investor: Entity) -> set[str]:
    return {normalized_name(name) for name in [investor.name, *investor.aliases]}


def _entity_fact_sources(investor: Entity, field: str) -> list[Source]:
    """Enriched facts require their own evidence; retain mapped-adapter compatibility."""
    sources = getattr(investor, f"{field}_sources")
    if sources or investor.metadata_status == "retrieved" or getattr(investor, f"{field}_basis"):
        return unique_sources(sources)
    return investor.sources


def statuses_for(investor: Entity, founder: FounderInput) -> list[InvestorStatus]:
    statuses: set[InvestorStatus] = set()
    for entry in founder.investor_statuses:
        # An explicit Cala ID disambiguates a name, even when another entity has that name.
        matches = entry.investor_id == investor.id if entry.investor_id else (
            normalized_name(entry.investor_name or "") in _entity_names(investor))
        if matches:
            statuses.update(entry.statuses)
    return sorted(statuses, key=lambda status: status.value)


def exclusion_for(investor: Entity, founder: FounderInput, lead: Entity,
                  profile: InvestorProfile | None = None) -> Exclusion | None:
    def exclusion(code: str, reason: str, sources: list[Source] | None = None):
        return Exclusion(investor=investor, code=code, reason=reason, sources=sources or [])

    statuses = statuses_for(investor, founder)
    # DNC takes precedence over every other founder-supplied status.
    for status, reason in (
        (InvestorStatus.DO_NOT_CONTACT, "Founder marked this investor do-not-contact."),
        (InvestorStatus.DECLINED, "Founder reports this investor declined this round."),
        (InvestorStatus.FIRM_COMMITTED, "Founder reports this investor is already firmly committed this round."),
    ):
        if status in statuses:
            return exclusion(status.value, reason)
    excluded_names = {normalized_name(value) for value in getattr(founder, "excluded_investors", [])}
    if _entity_names(investor) & excluded_names:
        return exclusion("already_in_excluded", "Founder listed this investor under 'Investors already in (excluded)'.")
    if investor.id == lead.id:
        return exclusion("primary_lead", "The primary lead is not a separate co-investor.", lead.sources)
    recognized_alias = (normalized_name(investor.name) in {normalized_name(n) for n in lead.aliases}
                        or normalized_name(lead.name) in {normalized_name(n) for n in investor.aliases})
    alias_sources = unique_sources(lead.sources + investor.sources)
    if alias_sources and (investor.id in lead.related_entity_ids or lead.id in investor.related_entity_ids or recognized_alias):
        return exclusion("lead_alias", "Cala identifies this entity as an alias or vehicle of the primary lead.",
                         alias_sources)
    preferences = founder.preferences
    type_sources = _entity_fact_sources(investor, "kind")
    country_sources = _entity_fact_sources(investor, "country")
    if type_sources and investor.kind == "angel" and not preferences.include_angels:
        return exclusion("investor_type_filter", "Founder excluded angel investors.", type_sources)
    if type_sources and investor.kind == "corporate_vc" and not preferences.include_corporate_vcs:
        return exclusion("investor_type_filter", "Founder excluded corporate VCs.", type_sources)
    if (preferences.investor_countries and investor.country and country_sources
            and normalize_country(investor.country) not in {
                normalize_country(country) for country in preferences.investor_countries}):
        return exclusion("investor_country_filter", "Investor country is outside the founder's explicit country filter.",
                         country_sources)
    if profile:
        for criterion in profile.ticket_criteria:
            if (criterion.kind in ("published_minimum_initial", "published_initial_range")
                    and criterion.currently_applicable and criterion.sources
                    and criterion.minimum_eur is not None
                    and criterion.minimum_eur > founder.round.remaining_eur):
                return exclusion("minimum_initial_ticket", "Applicable published minimum initial investment "
                                 f"of {_eur(criterion.minimum_eur)} exceeds the remaining allocation of "
                                 f"{_eur(founder.round.remaining_eur)}.", criterion.sources)
        for mandate in profile.mandates:
            if not (mandate.currently_applicable and mandate.sources):
                continue
            if (mandate.allowed_countries and founder.startup.country and normalize_country(founder.startup.country) not in {
                    normalize_country(country) for country in mandate.allowed_countries}):
                return exclusion("published_geography_mandate", "Applicable published mandate does not include the startup's country.",
                                 mandate.sources)
            if (mandate.allowed_stages and normalize_stage(founder.round.stage) not in {
                    normalize_stage(stage) for stage in mandate.allowed_stages}):
                return exclusion("published_stage_mandate", "Applicable published mandate does not include the startup's round stage.",
                                 mandate.sources)
    return None


def _dedup_rounds(rounds: list[FundingRound]) -> list[FundingRound]:
    """Merge event references; contradictory roles stay unknown, never last-wins.

    An event ID attached to conflicting companies or dates cannot safely support
    an observation. Other conflicting scalar facts are retained as unknown.
    """
    grouped: dict[str, list[FundingRound]] = {}
    for item in sorted(rounds, key=lambda r: (r.id, r.model_dump_json())):
        grouped.setdefault(item.id, []).append(item)
    result = []
    for entries in grouped.values():
        dates = {item.event_date for item in entries if item.event_date is not None}
        if len({item.company.id for item in entries}) > 1 or len(dates) > 1:
            continue
        merged = entries[0].model_copy(deep=True)
        merged.sources = unique_sources([source for item in entries for source in item.sources])
        people = {}
        roles: dict[str, set[bool]] = {}
        for item in entries:
            for person in item.participants:
                people.setdefault(person.investor.id, person.model_copy(deep=True))
                roles.setdefault(person.investor.id, set())
                if person.is_lead is not None:
                    roles[person.investor.id].add(person.is_lead)
        for investor_id, person in people.items():
            known = roles[investor_id]
            person.is_lead = next(iter(known)) if len(known) == 1 else None
        merged.participants = [people[key] for key in sorted(people)]
        for field in ("event_date", "stage", "sector", "company_country", "total_amount_eur"):
            values = {getattr(item, field) for item in entries if getattr(item, field) is not None}
            setattr(merged, field, next(iter(values)) if len(values) == 1 else None)
        result.append(merged)
    return result


def deduplicate_rounds(rounds: list[FundingRound]) -> list[FundingRound]:
    """Canonical identity and conflict handling shared by graph and scoring."""
    return _dedup_rounds(rounds)


def _behavior_observations(profile: InvestorProfile, shared: list[FundingRound]) -> list[InvestmentObservation]:
    """Investor behavior does not require a known issuer company or shared lead.

    Full funding rounds and native investor-event observations use the same Cala
    event ID, so one participation is counted once. Contradictory known values
    become unknown; the input order never chooses a role, date, or stage.
    """
    raw = [item for item in profile.observations if item.sources]
    for funding in profile.rounds + shared:
        if not funding.sources:
            continue
        for participation in funding.participants:
            if participation.investor.id == profile.investor.id:
                raw.append(InvestmentObservation(
                    id=funding.id, event_date=funding.event_date, stage=funding.stage,
                    is_lead=participation.is_lead, sources=funding.sources,
                ))
    grouped: dict[str, list[InvestmentObservation]] = {}
    for item in raw:
        grouped.setdefault(item.id, []).append(item)
    merged = []
    for event_id in sorted(grouped):
        entries = grouped[event_id]
        dates = {item.event_date for item in entries if item.event_date is not None}
        roles = {item.is_lead for item in entries if item.is_lead is not None}
        stages = {normalize_stage(item.stage) for item in entries if item.stage}
        merged.append(InvestmentObservation(
            id=event_id,
            event_date=next(iter(dates)) if len(dates) == 1 else None,
            stage=next(iter(stages)) if len(stages) == 1 else None,
            is_lead=next(iter(roles)) if len(roles) == 1 else None,
            sources=_sources(entries),
        ))
    return merged


def _eur(value: Decimal) -> str:
    return f"EUR {value:,.2f}".removesuffix(".00")


def _shared_phrase(deals: list[FundingRound], limit: int = 2) -> str:
    phrases = []
    for item in deals[:limit]:
        stage = f" {item.stage}" if item.stage else " funding"
        if item.event_date and (item.event_date_precision or "").lower() == "year":
            when = f" in {item.event_date.year}"
        elif item.event_date and (item.event_date_precision or "").lower() == "month":
            when = f" in {item.event_date.strftime('%Y-%m')}"
        else:
            when = f" on {item.event_date.isoformat()}" if item.event_date else ""
        phrases.append(f"{item.company.name}'s{stage} round{when}")
    return " and ".join(phrases)


def _origin_reason(origins: list[str], rounds: list[FundingRound], founder: FounderInput, investor: Entity, lead_reason: str) -> str:
    bits = []
    own = [r for r in rounds if r.company.id == founder.startup.cala_entity_id]
    previous = [r for r in rounds if r.company.id != founder.startup.cala_entity_id]
    if "existing_investor" in origins and own:
        bits.append(f"Existing investor in {founder.startup.company_name} ({_shared_phrase(own, 1)})")
    if "previous_company_backer" in origins and previous:
        bits.append(f"Backed {previous[0].company.name}, a founder's previous company")
    if "lead_network" in origins:
        bits.append(lead_reason)
    elif lead_reason != "Limited data in Cala":
        # build_reason starts with the (absent) lead connection; keep its activity parts.
        bits.extend(part for part in lead_reason.split("; ") if not part.startswith("Co-invested with"))
    return "; ".join(dict.fromkeys(bits)) or "Limited data in Cala"


def _direct_draft(card: CandidateCard, founder: FounderInput, lead: Entity) -> str:
    """Founder-network investors with no shared round: write to the investor directly."""
    own = [r for r in card.origin_rounds if r.company.id == founder.startup.cala_entity_id]
    previous = [r for r in card.origin_rounds if r.company.id != founder.startup.cala_entity_id]
    lead_note = (f" {lead.name} has provisionally agreed to lead." if founder.round.lead_status == "conditional"
                 else f" We are in discussions with {lead.name} as lead; their commitment is not yet confirmed and is provisional."
                 if founder.round.lead_status == "unconfirmed" else f" {lead.name} is leading.")
    context = (f"We are raising a {founder.round.stage.replace('_', ' ')} round for {founder.startup.company_name}, "
               f"with {_eur(founder.round.remaining_eur)} remaining after firm commitments.{lead_note} ")
    if own:
        opening = (f"Thank you again for backing {founder.startup.company_name} "
                   f"(Cala records your participation in {_shared_phrase(own, 1)}). ")
        ask = "Would you consider participating in this round, and what would you need from us to decide?"
    elif previous:
        opening = (f"Cala records your participation in {_shared_phrase(previous, 1)}, "
                   f"a company our founding team built before {founder.startup.company_name}. ")
        ask = "Would you be open to a short conversation about this round?"
    else:
        opening, ask = "", "Would you be open to a short conversation about this round?"
    signature = f"\n\n{founder.founder_name}" if founder.founder_name else ""
    return f"Hi {card.investor.name},\n\n{opening}{context}{ask}{signature}"


def _draft(card: CandidateCard, founder: FounderInput, lead: Entity) -> str:
    if not card.shared_deals:
        return _direct_draft(card, founder, lead)
    opening = ("While your lead commitment is provisional, " if founder.round.lead_status == "conditional"
               else "Before a lead commitment is confirmed, " if founder.round.lead_status == "unconfirmed" else "")
    if card.action == "follow_up" and InvestorStatus.EXISTING_SHAREHOLDER in card.statuses:
        request = f"could you advise on a follow-on request to our existing shareholder {card.investor.name}"
    elif card.action == "follow_up":
        request = f"could you help us with our existing conversation with {card.investor.name}"
    else:
        request = f"could you introduce us to {card.investor.name}"
    if not opening:
        request = request[0].upper() + request[1:]
    shared = _shared_phrase(card.shared_deals)
    context = (f"We are raising a {founder.round.stage.replace('_', ' ')} round for {founder.startup.company_name}, "
               f"with {_eur(founder.round.remaining_eur)} remaining after firm commitments. ")
    if founder.startup.product_description:
        context += f"Our product: {founder.startup.product_description} "
    elif founder.startup.sector:
        context += f"Our sector: {founder.startup.sector}. "
    if card.action == "follow_up":
        ending = "Could you advise on the appropriate next step and whether your support would be helpful?"
    else:
        ending = "Would they be appropriate to approach, and would an introduction make sense?"
    signature = f"\n\n{founder.founder_name}" if founder.founder_name else ""
    path = ""
    if card.recommended_action in ("reach_out_via_contact", "advisor_angel") and card.action_contact is not None:
        path = (f"Public Cala records also show a professional overlap with {card.action_contact.name}; "
                "we have not confirmed a personal relationship. ")
    return (f"Hi {lead.name},\n\n{opening}{request}? "
            f"Cala shows you participated together in {shared}. {path}{context}{ending}{signature}")


def _sector_fact(funding: FundingRound) -> tuple[str | None, list[Source]]:
    if funding.sector:
        return funding.sector, funding.sources
    for field in funding.company.metadata:
        if field.name not in ("sector", "industry", "industries", "category", "vertical") or not field.sources:
            continue
        values = field.value if isinstance(field.value, list) else [field.value]
        labels = [value for value in values if isinstance(value, str) and value.strip()]
        if labels:
            return ", ".join(labels), field.sources
    return None, []


def score_candidate(profile: InvestorProfile, shared_rounds: list[FundingRound],
                    founder: FounderInput, lead: Entity, as_of: date,
                    known_rounds: list[FundingRound] | None = None,
                    origins: list[str] | None = None,
                    origin_rounds: list[FundingRound] | None = None,
                    context=None) -> CandidateCard:
    """Evaluate the rule set (app/rules.py) over sourced Cala evidence.

    Trust rules start as "not checked" and are re-evaluated once trust loops are
    measured (finalize_card). The score is the number of rules met.
    """
    investor = profile.investor
    shared = []
    for item in _dedup_rounds(shared_rounds):
        participants = {person.investor.id for person in item.participants}
        if (investor.id in participants and lead.id in participants and item.sources
                and item.event_date is not None and item.event_date <= as_of):
            shared.append(item)
    shared.sort(key=lambda item: (-item.event_date.toordinal(), item.id))
    origins = list(dict.fromkeys(origins or ["lead_network"]))
    origin_rounds = [item for item in _dedup_rounds(origin_rounds or []) if item.sources
                     and (item.event_date is None or item.event_date <= as_of)
                     and any(person.investor.id == investor.id for person in item.participants)]
    origin_rounds.sort(key=lambda item: (-(item.event_date or date.min).toordinal(), item.id))
    # A shared round with the lead is no longer required, but every card needs
    # sourced evidence for why it is here: a shared round or an origin round.
    if not shared and not origin_rounds:
        raise ValueError("Candidate needs a sourced shared round with the lead or a sourced round in the founder network")
    if not shared:
        origins = [origin for origin in origins if origin != "lead_network"]

    context = context or rule_book.RuleContext(startup_id=founder.startup.cala_entity_id)
    own_company = founder.startup.cala_entity_id
    all_rounds = [item for item in _dedup_rounds(profile.rounds + shared + origin_rounds + (known_rounds or []))
                  if item.sources and (item.event_date is None or item.event_date <= as_of)
                  and any(person.investor.id == investor.id for person in item.participants)]
    behaviour = _behavior_observations(profile, shared + origin_rounds)
    dated = [item for item in behaviour if item.event_date is not None and item.event_date <= as_of]
    lookback_start = months_before(as_of, 60)
    stage_obs = [item for item in behaviour if item.event_date is None or lookback_start <= item.event_date <= as_of]
    recent = [item for item in dated if (as_of - item.event_date).days <= rule_book.ACTIVE_DAYS]

    shared_companies = {item.company.id for item in shared}
    recent_cutoff = months_before(as_of, rule_book.RECENT_PARTNER_MONTHS)
    recent_shared = [item for item in shared if item.event_date >= recent_cutoff]
    own_rounds = [item for item in origin_rounds if item.company.id == own_company]
    previous_rounds = [item for item in origin_rounds if item.company.id != own_company]
    components = [
        rule_book.result("lead_coinvestor", bool(shared),
                         f"{len(shared)} shared round(s) with {lead.name}" if shared else f"no shared round with {lead.name} found",
                         _sources(shared)),
        rule_book.result("repeat_partner", len(shared_companies) >= 2,
                         f"shared rounds in {len(shared_companies)} {'company' if len(shared_companies) == 1 else 'companies'}",
                         _sources(shared)),
        rule_book.result("recent_partner", bool(recent_shared),
                         f"latest shared round {shared[0].event_date.isoformat()}" if shared else "no shared round",
                         _sources(recent_shared or shared)),
        rule_book.result("your_investor", bool(own_rounds) if (context.startup_rounds_checked or own_rounds) else None,
                         f"in {_shared_phrase(own_rounds, 1)}" if own_rounds else
                         ("not in your company's identified rounds" if context.startup_rounds_checked
                          else "your company's rounds were not read; confirm your company in Cala"),
                         _sources(own_rounds)),
        rule_book.result("backed_your_founder", bool(previous_rounds) if (context.previous_companies_checked or previous_rounds) else None,
                         f"backed {previous_rounds[0].company.name}" if previous_rounds else
                         ("did not back the founders' previous companies" if context.previous_companies_checked
                          else "no founder's previous company was found in Cala"),
                         _sources(previous_rounds)),
        rule_book.active_rule([item.event_date for item in dated], as_of, _sources(recent or dated)[:6]),
        *rule_book.fit_rules(investor, founder.startup.sector, founder.startup.country, founder.round.stage,
                             all_rounds, stage_obs, context, normalize_stage, normalize_country),
    ]
    competitors = {normalized_name(name) for name in founder.startup.known_competitors if name.strip()}
    conflicts = [item for item in all_rounds if normalized_name(item.company.name) in competitors
                 or (item.company.aliases and competitors & {normalized_name(a) for a in item.company.aliases})]
    flags = []
    if not competitors:
        components.append(rule_book.result("no_competitor", None, "no competitors named"))
    else:
        components.append(rule_book.result(
            "no_competitor", not conflicts,
            f"backed {conflicts[0].company.name}, which you named as a competitor" if conflicts
            else f"none of its {len(all_rounds)} identified rounds is in a named competitor",
            _sources(conflicts or all_rounds)[:6]))
        if conflicts:
            hit = conflicts[0]
            flags.append(InvestorFlag(kind="conflict", severity="red", company=hit.company.name, sources=_sources([hit]),
                                      reason=f"Backed competitor {hit.company.name} ({hit.stage or 'round'}, "
                                             f"{hit.event_date.isoformat() if hit.event_date else 'date unknown'})"))

    # Equal-share ticket estimate for the allocation panel (not a rule: EUR totals are rare).
    ticket_values = [item.total_amount_eur / Decimal(len({p.investor.id for p in item.participants}))
                     for item in all_rounds if item.total_amount_eur and item.participants
                     and item.event_date and months_before(as_of, 24) <= item.event_date]
    estimated_ticket = (median(ticket_values).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if ticket_values else None)
    ticket_basis = ("Equal-share heuristic: EUR funding-round total divided by observed investor count; "
                    "actual investor checks are unknown.") if estimated_ticket is not None else None
    considerations: list[Consideration] = []

    def add(code: str, content: str, evidence: list[Source], basis: str = "Cala fact"):
        if evidence or basis == "Founder supplied":
            considerations.append(Consideration(code=code, text=content, basis=basis, sources=unique_sources(evidence)))

    for flag in flags:
        add("founder_competitor", flag.reason + ". Competitor identity supplied by the founder.", flag.sources, "Founder supplied")
    companies = {item.company.id: item.company for item in all_rounds}
    for assessment in profile.assessments:
        if (assessment.company_id in companies and assessment.company_id != own_company
                and assessment.sources and assessment.overlap in ("adjacent_business", "possible_overlap", "direct_substitute")):
            add("portfolio_overlap", f"LLM assessment: {companies[assessment.company_id].name} - "
                f"{assessment.overlap.replace('_', ' ')}. {assessment.rationale}", assessment.sources, "LLM assessment")
    statuses = statuses_for(investor, founder)
    if statuses:
        add("existing_relationship", "Founder supplied: " + ", ".join(status.value.replace("_", " ") for status in statuses) + ".", [], "Founder supplied")
    if founder.preferences.investor_countries and not (investor.country and _entity_fact_sources(investor, "country")):
        add("country_unknown", "The founder's investor-country preference cannot be verified from sourced investor facts; confirm eligibility.", [], "Founder supplied")
    if ((not founder.preferences.include_angels or not founder.preferences.include_corporate_vcs)
            and not (investor.kind in ("vc", "angel", "corporate_vc") and _entity_fact_sources(investor, "kind"))):
        add("investor_type_unknown", "The founder's investor-type preference cannot be verified from sourced investor facts; confirm eligibility.", [], "Founder supplied")
    lead_reason = "Limited data in Cala"
    if shared:
        names = ", ".join(item.company.name for item in shared[:2])
        lead_reason = f"Co-invested with {lead.name} in {names}" + (f" and {len(shared) - 2} more" if len(shared) > 2 else "")
    card = CandidateCard(
        investor=investor, fit_score=0, profile_completeness=0,
        explanation=_origin_reason(origins, origin_rounds, founder, investor, lead_reason),
        shared_round_count=len(shared), shared_company_count=len(shared_companies),
        shared_deals=shared[:3], recent_rounds_12m=len(recent) if dated else None,
        most_recent_investment=max((item.event_date for item in dated), default=None),
        components=[], considerations=considerations, statuses=statuses, profile=profile,
        sources=unique_sources(investor.sources + profile.sources + _sources(all_rounds)
                               + [source for component in components for source in component.sources]),
        origins=origins, origin_rounds=origin_rounds[:4],
        action="follow_up" if "existing_investor" in origins or any(status in statuses for status in (InvestorStatus.EXISTING_SHAREHOLDER,
                                  InvestorStatus.ALREADY_APPROACHED, InvestorStatus.IN_CONVERSATION,
                                  InvestorStatus.CONDITIONAL_INTEREST)) else "introduction",
        review_status="flagged" if flags else "short", flags=flags, estimated_ticket_eur=estimated_ticket,
        ticket_estimate_basis=ticket_basis, draft="",
    )
    rule_book.apply(card, components + rule_book.trust_rules(card))
    card.draft = _draft(card, founder, lead)
    return card


def build_allocation(cards: list[CandidateCard], remaining: Decimal) -> list[AllocationItem]:
    """Main's greedy allocation, with exact EUR amounts and explicit proxy labels."""
    if remaining <= 0:
        return []
    eligible = [card for card in rank_candidates(cards) if card.review_status == "short"]
    rows = main_scoring.allocation([
        {"id": card.investor.id, "name": card.investor.name,
         "ticket": card.estimated_ticket_eur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
         if card.estimated_ticket_eur is not None else None}
        for card in eligible], remaining)
    result = []
    percentages = [(Decimal(str(row["eur"])) / remaining * 100).quantize(Decimal("0.0001")) for row in rows]
    # Apply display-rounding residue to the largest share, which avoids a tiny
    # negative final percentage when several preceding shares rounded upward.
    if percentages:
        largest = max(range(len(percentages)), key=percentages.__getitem__)
        percentages[largest] += Decimal(100) - sum(percentages)
    by_id = {card.investor.id: card for card in eligible}
    for index, row in enumerate(rows):
        amount = Decimal(str(row["eur"])).quantize(Decimal("0.01"))
        card = by_id.get(row["id"])
        basis = card.ticket_estimate_basis if card else "Unallocated remainder after available eligible EUR estimates."
        result.append(AllocationItem(investor_id=row["id"], name=row["name"], amount_eur=amount,
                                     percentage=float(percentages[index]), basis=basis or "Equal-share heuristic; actual investor checks unknown."))
    return result


def rank_candidates(cards: list[CandidateCard]) -> list[CandidateCard]:
    """Rules met, then Connection rules met, then most recent evidence, then name."""
    return ranker.rank(cards)


def finalize_card(card: CandidateCard, founder: FounderInput, lead: Entity) -> CandidateCard:
    """Re-evaluate the trust rules, set the rule-based action and refresh the draft."""
    rule_book.apply(card, rule_book.trust_rules(card))
    action, contact = ranker.recommended_action(card)
    card.recommended_action, card.action_contact = action, contact
    card.draft = _draft(card, founder, lead)
    return card


def combined_draft(cards: list[CandidateCard], founder: FounderInput, lead: Entity) -> str:
    # The combined request asks the lead, so it only covers investors connected to the lead.
    chosen = [card for card in rank_candidates(cards) if card.review_status == "short" and card.shared_deals][:3]
    if not chosen:
        return ""
    provisional = (" Your lead commitment is provisional." if founder.round.lead_status == "conditional"
                   else " The selected lead's commitment to this round has not been confirmed."
                   if founder.round.lead_status == "unconfirmed" else "")
    lines = [f"Hi {lead.name},", "", f"We are raising a {founder.round.stage.replace('_', ' ')} round for "
             f"{founder.startup.company_name}, with {_eur(founder.round.remaining_eur)} remaining after firm commitments.{provisional}",
             "Could you advise whether these introductions or next steps would be appropriate?", ""]
    for card in chosen:
        if card.action == "follow_up" and InvestorStatus.EXISTING_SHAREHOLDER in card.statuses:
            action = "advice on a follow-on request to our existing shareholder"
        else:
            action = "help with our existing conversation" if card.action == "follow_up" else "an introduction"
        lines.append(f"- {card.investor.name}: {action}. Cala shows shared participation in {_shared_phrase(card.shared_deals, 1)}.")
    lines += ["", "Please let us know which approaches make sense."]
    if founder.founder_name:
        lines += ["", founder.founder_name]
    return "\n".join(lines)
