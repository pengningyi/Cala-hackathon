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

from . import main_scoring
from .entity_metadata import country_name
from .models import (
    AllocationItem, CandidateCard, Consideration, Entity, Exclusion, FounderInput, FundingRound,
    InvestmentObservation, InvestorFlag, InvestorProfile, InvestorStatus, ScoreComponent, Source,
)


WEIGHTS = {name: main_scoring.WEIGHTS[key] for name, key in {
    "co_investment": "coinv", "recent_activity": "activity", "stage_fit": "stage",
    "sector_fit": "sector", "non_lead_participation": "follow", "geography_fit": "geo",
}.items()}


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


def _component(name: str, value: float | None, explanation: str, sources: list[Source],
               numerator: float | None = None, denominator: float | None = None) -> ScoreComponent:
    known = value is not None and bool(sources)
    return ScoreComponent(name=name, weight=WEIGHTS[name], value=value if value is not None else 0,
                          known=known, explanation=explanation if known else "Unknown; contributes zero under main-branch scoring.",
                          numerator=numerator if known else None, denominator=denominator if known else None,
                          sources=unique_sources(sources) if known else [])


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


def _draft(card: CandidateCard, founder: FounderInput, lead: Entity) -> str:
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
    return (f"Hi {lead.name},\n\n{opening}{request}? "
            f"Cala shows you participated together in {shared}. {context}{ending}{signature}")


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
                    founder: FounderInput, lead: Entity, as_of: date) -> CandidateCard:
    """Use main's numerical rules while retaining branch-one's exact-event evidence."""
    investor = profile.investor
    shared = []
    for item in _dedup_rounds(shared_rounds):
        participants = {person.investor.id for person in item.participants}
        if (investor.id in participants and lead.id in participants and item.sources
                and item.event_date is not None and item.event_date <= as_of):
            shared.append(item)
    shared.sort(key=lambda item: (-item.event_date.toordinal(), item.id))
    if not shared:
        raise ValueError("Candidate must have a sourced, dated funding event shared with the confirmed lead")

    known_events = [item for item in _dedup_rounds(profile.rounds + shared)
                    if item.event_date is not None and item.event_date <= as_of and item.sources
                    and any(person.investor.id == investor.id for person in item.participants)]
    observed = [item for item in known_events if months_before(as_of, 24) <= item.event_date]
    known_behavior = [item for item in _behavior_observations(profile, shared)
                      if item.event_date is not None and item.event_date <= as_of]
    observed_behavior = [item for item in known_behavior if item.event_date >= months_before(as_of, 24)]
    events_by_id = {item.id: item for item in observed}
    rows = []
    sector_sources: list[Source] = []
    geography_sources: list[Source] = []
    ticket_values: list[Decimal] = []
    for observation in observed_behavior:
        funding = events_by_id.get(observation.id)
        sector, sector_evidence = _sector_fact(funding) if funding else (None, [])
        country, country_evidence = None, []
        if funding:
            country = funding.company_country or funding.company.country
            country_evidence = funding.sources if funding.company_country else _entity_fact_sources(funding.company, "country")
            if not country_evidence:
                country = None
        investor_ids = sorted({person.investor.id for person in funding.participants}) if funding else []
        amount = funding.total_amount_eur if funding and investor_ids else None
        if amount is not None and amount > 0:
            ticket_values.append(amount / Decimal(len(investor_ids)))
        sector_sources.extend(sector_evidence)
        geography_sources.extend(country_evidence)
        rows.append({"company": funding.company.name if funding else "", "date": observation.event_date.isoformat(),
                     "stage": observation.stage, "sector": sector, "country": normalize_country(country) if country else "",
                     "lead": observation.is_lead, "amount": float(amount) if amount is not None else None,
                     "investors": investor_ids})

    sector_terms = [term.strip() for term in re.split(r"[,/;]| and ", founder.startup.sector.lower())
                    + [founder.startup.sector.lower()] if len(term.strip()) > 2]
    payload = {"id": investor.id, "name": investor.name, "origin": "lead-graph", "rounds": rows,
               "shared": [{"company": item.company.name, "stage": item.stage, "date": item.event_date.isoformat()}
                          for item in shared]}
    context = {"stage": founder.round.stage, "sector_terms": sector_terms,
               "country": normalize_country(founder.startup.country), "open_eur": float(founder.round.remaining_eur),
               "competitors": founder.startup.known_competitors}
    main_scoring.score_candidate(payload, context, as_of=as_of)
    signals = payload["signals"]
    n = len(observed_behavior)
    recent = [item for item in observed_behavior if (as_of - item.event_date).days <= 365]
    staged = [item for item in observed_behavior if item.stage]
    matching = [item for item in observed_behavior if normalize_stage(item.stage) == normalize_stage(founder.round.stage)]
    known_roles = [item for item in observed_behavior if item.is_lead is not None]
    non_lead = sum(not item.is_lead for item in known_roles)
    recent_shared = sum(item.event_date >= date(as_of.year - 2, as_of.month, 1) for item in shared)
    activity_sources = _sources(observed_behavior) or (profile.sources if profile.activity_window_complete else [])
    components = [
        _component("co_investment", signals["coinv"]["v"], signals["coinv"]["label"], _sources(shared),
                   min(len(shared) + .5 * recent_shared, 3), 3),
        _component("recent_activity", signals["activity"]["v"], signals["activity"]["label"], activity_sources, len(recent), 4),
        _component("stage_fit", signals["stage"]["v"], signals["stage"]["label"], _sources(staged), len(matching), n),
        _component("sector_fit", signals["sector"]["v"], signals["sector"]["label"], sector_sources,
                   sum(any(term in str(row["sector"]).lower() for term in sector_terms) for row in rows), n),
        _component("non_lead_participation", signals["follow"]["v"], signals["follow"]["label"], _sources(known_roles),
                   non_lead, len(known_roles)),
        _component("geography_fit", signals.get("geo", {}).get("v"), signals.get("geo", {}).get("label", "geography unknown"),
                   geography_sources),
    ]
    estimated_ticket = (median(ticket_values).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if ticket_values else None)
    ticket_basis = ("Equal-share heuristic: EUR funding-round total divided by observed investor count; "
                    "actual investor checks are unknown.") if estimated_ticket is not None else None
    flags = []
    for flag in payload["flags"]:
        if flag["kind"] == "conflict":
            evidence = unique_sources([source for item in observed if item.company.name == flag.get("company") for source in item.sources])
        elif flag["kind"] == "ticket":
            evidence = _sources([item for item in observed if item.total_amount_eur is not None])
        else:
            evidence = _sources(observed_behavior)
        reason = flag["reason"]
        if flag["kind"] == "ticket":
            reason = (f"The equal-share estimate exceeds the open allocation before display rounding "
                      f"(rounded estimate {_eur(estimated_ticket)}; open {_eur(founder.round.remaining_eur)}). "
                      "Actual investor checks are unknown.")
        flags.append(InvestorFlag(kind=flag["kind"], severity=flag["severity"], reason=reason,
                                  company=flag.get("company"), sources=evidence))
    considerations: list[Consideration] = []

    def add(code: str, content: str, evidence: list[Source], basis: str = "Cala fact"):
        if evidence or basis == "Founder supplied":
            considerations.append(Consideration(code=code, text=content, basis=basis, sources=unique_sources(evidence)))

    codes = {"inactive": "low_recent_activity", "lead-only": "usually_leads", "stage": "limited_stage_history"}
    for flag in flags:
        if flag.kind in codes:
            add(codes[flag.kind], flag.reason, flag.sources)
        elif flag.kind == "conflict":
            add("founder_competitor", flag.reason + ". Competitor identity supplied by the founder.", flag.sources, "Founder supplied")
    companies = {item.company.id: item.company for item in observed}
    for assessment in profile.assessments:
        if (assessment.company_id in companies and assessment.company_id != founder.startup.cala_entity_id
                and assessment.sources and assessment.overlap in ("adjacent_business", "possible_overlap", "direct_substitute")):
            add("portfolio_overlap", f"LLM assessment: {companies[assessment.company_id].name} ? "
                f"{assessment.overlap.replace('_', ' ')}. {assessment.rationale}", assessment.sources, "LLM assessment")
    for criterion in profile.ticket_criteria:
        lo, hi = criterion.minimum_eur, criterion.maximum_eur
        above = lo is not None and lo > founder.round.remaining_eur
        broad = lo is not None and hi is not None and hi > lo and (lo == 0 or hi >= 2 * lo)
        if criterion.sources and (above or broad or not criterion.currently_applicable):
            amount = f"{_eur(lo) if lo is not None else 'unknown'} to {_eur(hi) if hi is not None else 'unknown'}"
            add("participation_size", f"{criterion.kind.replace('_', ' ')}: {amount}; confirm current applicability and whether a smaller participation is possible.", criterion.sources)
    for mandate in profile.mandates:
        if mandate.sources and not mandate.currently_applicable:
            add("historical_mandate", "Published mandate has unclear current applicability; confirm current stage and geography appetite.", mandate.sources)
    statuses = statuses_for(investor, founder)
    if statuses:
        add("existing_relationship", "Founder supplied: " + ", ".join(status.value.replace("_", " ") for status in statuses) + ".", [], "Founder supplied")
    if founder.preferences.investor_countries and not (investor.country and _entity_fact_sources(investor, "country")):
        add("country_unknown", "The founder's investor-country preference cannot be verified from sourced investor facts; confirm eligibility.", [], "Founder supplied")
    if ((not founder.preferences.include_angels or not founder.preferences.include_corporate_vcs)
            and not (investor.kind in ("vc", "angel", "corporate_vc") and _entity_fact_sources(investor, "kind"))):
        add("investor_type_unknown", "The founder's investor-type preference cannot be verified from sourced investor facts; confirm eligibility.", [], "Founder supplied")
    card = CandidateCard(
        investor=investor, fit_score=payload["score"],
        profile_completeness=sum(item.weight for item in components if item.known) / 100,
        explanation=main_scoring.build_reason(payload, lead.name),
        shared_round_count=len(shared), shared_company_count=len({item.company.id for item in shared}),
        shared_deals=shared[:3], recent_rounds_12m=payload["rounds_12m"],
        most_recent_investment=max((item.event_date for item in known_behavior), default=None),
        components=components, considerations=considerations, statuses=statuses, profile=profile,
        sources=unique_sources(investor.sources + profile.sources + _sources(shared + observed) + _sources(known_behavior)
                               + [source for component in components for source in component.sources]),
        action="follow_up" if any(status in statuses for status in (InvestorStatus.EXISTING_SHAREHOLDER,
                                  InvestorStatus.ALREADY_APPROACHED, InvestorStatus.IN_CONVERSATION,
                                  InvestorStatus.CONDITIONAL_INTEREST)) else "introduction",
        review_status=payload["status"], flags=flags, estimated_ticket_eur=estimated_ticket,
        ticket_estimate_basis=ticket_basis, draft="",
    )
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
    def key(card: CandidateCard):
        connection = next(c.value for c in card.components if c.name == "co_investment")
        most_recent = max((r.event_date for r in card.shared_deals if r.event_date), default=date.min)
        return (card.review_status != "short", -card.fit_score, -connection, -most_recent.toordinal(), normalized_name(card.investor.name), card.investor.id)
    return sorted(cards, key=key)


def combined_draft(cards: list[CandidateCard], founder: FounderInput, lead: Entity) -> str:
    chosen = [card for card in rank_candidates(cards) if card.review_status == "short"][:3]
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
