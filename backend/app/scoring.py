"""Frozen PRD-v1 ranking and policy rules over Cala profile snapshots.

No public facts or investor check sizes are inferred here. Semantic assessments
are stored on the snapshot and remain explicitly labelled as LLM assessments.
"""
from __future__ import annotations

import calendar
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal

from .models import (
    CandidateCard, Consideration, Entity, Exclusion, FounderInput, FundingRound,
    InvestmentObservation, InvestorProfile, InvestorStatus, ScoreComponent, Source,
)


WEIGHTS = {"co_investment": 35, "recent_activity": 20, "stage_fit": 15,
           "sector_fit": 15, "non_lead_participation": 10, "geography_fit": 5}
USUALLY_LEADS_THRESHOLD = 0.8
LIMITED_STAGE_THRESHOLD = 0.1


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
_REGIONS = {
    "europe": set("albania|andorra|austria|belarus|belgium|bosnia and herzegovina|bulgaria|croatia|cyprus|czechia|denmark|estonia|finland|france|germany|greece|hungary|iceland|ireland|italy|kosovo|latvia|liechtenstein|lithuania|luxembourg|malta|moldova|monaco|montenegro|netherlands|north macedonia|norway|poland|portugal|romania|san marino|serbia|slovakia|slovenia|spain|sweden|switzerland|ukraine|united kingdom|vatican city".split("|")),
    "north_america": {"united states", "canada", "mexico"},
    "oceania": {"australia", "new zealand"},
    "asia": set("china|india|japan|singapore|south korea|taiwan|indonesia|malaysia|thailand|vietnam|philippines|pakistan|bangladesh".split("|")),
    "middle_east": {"israel", "united arab emirates", "saudi arabia", "qatar", "bahrain", "jordan", "turkey"},
    "south_america": {"brazil", "argentina", "chile", "colombia", "peru", "uruguay"},
    "africa": {"south africa", "nigeria", "kenya", "egypt", "ghana", "morocco", "tunisia", "rwanda"},
}


def normalize_country(value: str | None) -> str:
    norm = normalized_name(value or "")
    return _COUNTRY_ALIASES.get(norm, norm)


def normalize_stage(value: str | None) -> str:
    norm = normalized_name(value or "")
    return {"series a": "series_a", "series_a": "series_a", "a": "series_a",
            "seed round": "seed", "seed": "seed"}.get(norm, norm)


def months_before(day: date, months: int) -> date:
    month_number = day.year * 12 + day.month - 1 - months
    year, month = divmod(month_number, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _region(country: str) -> str | None:
    return next((region for region, countries in _REGIONS.items() if country in countries), None)


def unique_sources(sources: list[Source]) -> list[Source]:
    unique = {(str(source.url), source.retrieved_at.isoformat(), str(source.published_at)): source
              for source in sources}
    return [unique[key] for key in sorted(unique)]


def _sources(rounds: list[FundingRound] | list[InvestmentObservation]) -> list[Source]:
    return unique_sources([source for item in rounds for source in item.sources])


def _entity_names(investor: Entity) -> set[str]:
    return {normalized_name(name) for name in [investor.name, *investor.aliases]}


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
    if investor.id == lead.id:
        return exclusion("primary_lead", "The primary lead is not a separate co-investor.", lead.sources)
    recognized_alias = (normalized_name(investor.name) in {normalized_name(n) for n in lead.aliases}
                        or normalized_name(lead.name) in {normalized_name(n) for n in investor.aliases})
    alias_sources = unique_sources(lead.sources + investor.sources)
    if alias_sources and (investor.id in lead.related_entity_ids or lead.id in investor.related_entity_ids or recognized_alias):
        return exclusion("lead_alias", "Cala identifies this entity as an alias or vehicle of the primary lead.",
                         alias_sources)
    preferences = founder.preferences
    if investor.sources and investor.kind == "angel" and not preferences.include_angels:
        return exclusion("investor_type_filter", "Founder excluded angel investors.", investor.sources)
    if investor.sources and investor.kind == "corporate_vc" and not preferences.include_corporate_vcs:
        return exclusion("investor_type_filter", "Founder excluded corporate VCs.", investor.sources)
    if (preferences.investor_countries and investor.country and investor.sources
            and normalize_country(investor.country) not in {
                normalize_country(country) for country in preferences.investor_countries}):
        return exclusion("investor_country_filter", "Investor country is outside the founder's explicit country filter.",
                         investor.sources)
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
            if (mandate.allowed_countries and normalize_country(founder.startup.country) not in {
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
    return ScoreComponent(name=name, weight=WEIGHTS[name], value=value if known else 0.5,
                          known=known, explanation=explanation if known else "Unknown; neutral scoring value 0.5.",
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
    opening = ("While your lead commitment is provisional, " if founder.round.lead_status == "conditional" else "")
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
               f"with {_eur(founder.round.remaining_eur)} remaining after firm commitments. "
               f"Our product: {founder.startup.product_description} ")
    if card.action == "follow_up":
        ending = "Could you advise on the appropriate next step and whether your support would be helpful?"
    else:
        ending = "Would they be appropriate to approach, and would an introduction make sense?"
    signature = f"\n\n{founder.founder_name}" if founder.founder_name else ""
    return (f"Hi {lead.name},\n\n{opening}{request}? "
            f"Cala shows you participated together in {shared}. {context}{ending}{signature}")


def score_candidate(profile: InvestorProfile, shared_rounds: list[FundingRound],
                    founder: FounderInput, lead: Entity, as_of: date) -> CandidateCard:
    investor = profile.investor
    shared = []
    for item in _dedup_rounds(shared_rounds):
        participants = {p.investor.id for p in item.participants}
        if (investor.id in participants and lead.id in participants and item.sources
                and item.event_date is not None and item.event_date <= as_of):
            shared.append(item)
    shared.sort(key=lambda r: (-(r.event_date or date.min).toordinal(), r.id))
    if not shared:
        raise ValueError("Candidate must have a sourced, dated funding event shared with the confirmed lead")
    latest_by_company: dict[str, FundingRound] = {}
    for item in shared:
        latest_by_company.setdefault(item.company.id, item)
    recency = sum(2 ** (-max(0, (as_of - item.event_date).days / (365.2425 / 12)) / 24)
                  for item in latest_by_company.values())
    connection = _component("co_investment", min(recency, 3) / 3,
                            f"{len(shared)} shared rounds across {len(latest_by_company)} distinct companies; latest round per company, 24-month half-life.",
                            _sources(shared), min(recency, 3), 3)

    known_events = [item for item in _dedup_rounds(profile.rounds + shared)
                    if item.event_date is not None and item.event_date <= as_of and item.sources
                    and any(p.investor.id == investor.id for p in item.participants)]
    observed = [item for item in known_events if months_before(as_of, 24) <= item.event_date]
    known_behavior = [item for item in _behavior_observations(profile, shared)
                      if item.event_date is not None and item.event_date <= as_of]
    observed_behavior = [item for item in known_behavior if item.event_date >= months_before(as_of, 24)]
    recent = [item for item in observed_behavior if item.event_date >= months_before(as_of, 12)]
    activity_known = bool(observed_behavior) or profile.activity_window_complete
    activity_sources = _sources(observed_behavior) or profile.sources
    activity = _component("recent_activity", min(len(recent) / 4, 1) if activity_known else None,
                          f"{len(recent)} observed rounds in the last 12 months; capped at four.", activity_sources,
                          len(recent), 4)
    staged = [item for item in observed_behavior if item.stage]
    matching = [item for item in staged if normalize_stage(item.stage) == normalize_stage(founder.round.stage)]
    stage = _component("stage_fit", len(matching) / len(staged) if staged else None,
                       f"{len(matching)}/{len(staged)} known-stage rounds match {founder.round.stage} in the last 24 months.",
                       _sources(staged), len(matching), len(staged))

    # One frozen assessment per distinct observed company, never a count of round references.
    company_ids = {item.company.id for item in observed}
    assessments = {}
    for assessment in sorted(profile.assessments, key=lambda a: a.model_dump_json()):
        if assessment.company_id in company_ids and assessment.sources:
            assessments.setdefault(assessment.company_id, assessment)
    evaluated = [a for a in assessments.values() if a.sector_fit != "unknown"]
    sector_sum = sum({"same": 1, "adjacent": 0.5, "unrelated": 0}[a.sector_fit] for a in evaluated)
    sector = _component("sector_fit", sector_sum / len(evaluated) if evaluated else None,
                        f"LLM assessment: weighted fit total {sector_sum:g}/{len(evaluated)} evaluated portfolio companies.",
                        [s for a in evaluated for s in a.sources], sector_sum, len(evaluated))
    known_roles = [item for item in observed_behavior if item.is_lead is not None]
    non_lead = sum(not item.is_lead for item in known_roles)
    role = _component("non_lead_participation", non_lead / len(known_roles) if known_roles else None,
                      f"{non_lead}/{len(known_roles)} participations with known lead status were explicitly non-lead.",
                      _sources(known_roles), non_lead, len(known_roles))

    geographies = [(item, normalize_country(item.company_country or item.company.country))
                   for item in observed if item.company_country or item.company.country]
    founder_country = normalize_country(founder.startup.country)
    founder_region = _region(founder_country)
    if not geographies:
        geo_value = None
    elif any(country == founder_country for _, country in geographies):
        geo_value = 1.0
    elif founder_region and any(_region(country) == founder_region for _, country in geographies):
        geo_value = 0.5
    else:
        geo_value = 0.0
    geo = _component("geography_fit", geo_value,
                     "Prior country investment = 1; same predefined region = 0.5; other observed countries = 0.",
                     _sources([item for item, _ in geographies]))
    components = [connection, activity, stage, sector, role, geo]
    considerations: list[Consideration] = []

    def add(code: str, text: str, sources: list[Source], basis: str = "Cala fact"):
        if sources or basis == "Founder supplied":
            considerations.append(Consideration(code=code, text=text, basis=basis, sources=unique_sources(sources)))

    if activity.known and not recent:
        add("low_recent_activity", "No recent investment observed; confirm current appetite.", activity.sources)
    led = len(known_roles) - non_lead
    if known_roles and led / len(known_roles) > USUALLY_LEADS_THRESHOLD:
        add("usually_leads", f"Led {led}/{len(known_roles)} participations with known roles; ask whether participation alongside this lead is possible.", role.sources)
    if staged and len(matching) / len(staged) < LIMITED_STAGE_THRESHOLD:
        add("limited_stage_history", f"Only {len(matching)}/{len(staged)} known-stage rounds match; ask about the current mandate.", stage.sources)
    companies = {item.company.id: item.company for item in observed}
    for assessment in assessments.values():
        if assessment.company_id == founder.startup.cala_entity_id:
            continue
        if assessment.overlap in ("adjacent_business", "possible_overlap", "direct_substitute"):
            add("portfolio_overlap", f"LLM assessment: {companies[assessment.company_id].name} — "
                f"{assessment.overlap.replace('_', ' ')}. {assessment.rationale}", assessment.sources, "LLM assessment")
    for company in companies.values():
        if normalized_name(company.name) in {normalized_name(name) for name in founder.startup.known_competitors}:
            add("founder_competitor", f"Founder supplied: {company.name} is a known competitor. Confirm whether the overlap affects an introduction.",
                company.sources, "Founder supplied")
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
        add("existing_relationship", "Founder supplied: " + ", ".join(s.value.replace("_", " ") for s in statuses) + ".", [], "Founder supplied")
    if founder.preferences.investor_countries and not (investor.country and investor.sources):
        add("country_unknown", "The founder's investor-country preference cannot be verified from sourced investor facts; confirm eligibility.",
            [], "Founder supplied")
    if ((not founder.preferences.include_angels or not founder.preferences.include_corporate_vcs)
            and not (investor.kind != "unknown" and investor.sources)):
        add("investor_type_unknown", "The founder's investor-type preference cannot be verified from sourced investor facts; confirm eligibility.",
            [], "Founder supplied")
    card = CandidateCard(
        investor=investor,
        fit_score=int(math.floor(sum(c.weight * c.value for c in components) + 0.5)),
        profile_completeness=sum(c.weight for c in components if c.known) / 100,
        explanation=f"{len(shared)} shared rounds across {len(latest_by_company)} companies; score prioritizes introductions and is not an investment probability.",
        shared_round_count=len(shared), shared_company_count=len(latest_by_company), shared_deals=shared[:3],
        recent_rounds_12m=len(recent) if activity.known else None,
        most_recent_investment=max((item.event_date for item in known_behavior), default=None),
        components=components, considerations=considerations, statuses=statuses, profile=profile,
        sources=unique_sources(investor.sources + profile.sources + _sources(shared + observed) + _sources(known_behavior)
                               + [s for component in components for s in component.sources]),
        action="follow_up" if any(s in statuses for s in (InvestorStatus.EXISTING_SHAREHOLDER,
                                  InvestorStatus.ALREADY_APPROACHED, InvestorStatus.IN_CONVERSATION,
                                  InvestorStatus.CONDITIONAL_INTEREST)) else "introduction",
        draft="",
    )
    card.draft = _draft(card, founder, lead)
    return card


def rank_candidates(cards: list[CandidateCard]) -> list[CandidateCard]:
    def key(card: CandidateCard):
        connection = next(c.value for c in card.components if c.name == "co_investment")
        most_recent = max((r.event_date for r in card.shared_deals if r.event_date), default=date.min)
        return (-card.fit_score, -connection, -most_recent.toordinal(), normalized_name(card.investor.name), card.investor.id)
    return sorted(cards, key=key)


def combined_draft(cards: list[CandidateCard], founder: FounderInput, lead: Entity) -> str:
    chosen = rank_candidates(cards)[:3]
    if not chosen:
        return ""
    provisional = " Your lead commitment is provisional." if founder.round.lead_status == "conditional" else ""
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
