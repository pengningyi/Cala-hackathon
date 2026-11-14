"""Local, sourced help for a completed result; no model or Cala calls.

Supported intents: priorities/why, comparison, graph connections, saved drafts,
participation-size unknowns, overlap/considerations, exclusions and completeness.
Unrecognized questions receive scope guidance. The stored run is never mutated.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .models import CandidateCard, ChatRequest, ChatResponse, RunResult, Source
from .scoring import months_before


def _unique_sources(sources: list[Source], limit: int = 16) -> list[Source]:
    found = {}
    for source in sources:
        found.setdefault((str(source.url), source.published_at, source.retrieved_at), source)
    return list(found.values())[:limit]


def _balanced_sources(groups: list[list[Source]], limit: int = 16) -> list[Source]:
    """Reserve evidence for every discussed card before adding more references."""
    unique_groups = [_unique_sources(group) for group in groups if group]
    ordered = [group[index] for index in range(max(map(len, unique_groups), default=0))
               for group in unique_groups if index < len(group)]
    return _unique_sources(ordered, limit)


def _shared_sources(card: CandidateCard) -> list[Source]:
    # Put one reference per described event ahead of repeated references to the
    # first deal, so saved requests discussing two events can cite both.
    groups = [deal.sources for deal in card.shared_deals if deal.sources]
    return _balanced_sources(groups)


def _summary_sources(card: CandidateCard, as_of: date) -> list[Source]:
    """Support shared-history and activity claims with their own observations."""
    shared = _shared_sources(card)
    connection = next((item for item in card.components if item.name == "co_investment"), None)
    activity = next((item for item in card.components if item.name == "recent_activity"), None)
    primary = shared[:1] or (connection.sources[:1] if connection else [])
    activity_refs = []
    if card.recent_rounds_12m is not None and activity is not None and activity.known:
        cutoff = months_before(as_of, 12)
        observations = [(item.event_date, item.sources) for item in card.profile.observations
                        if item.event_date and cutoff <= item.event_date <= as_of and item.sources]
        observations.extend((item.event_date, item.sources) for item in card.profile.rounds
                            if item.event_date and cutoff <= item.event_date <= as_of and item.sources
                            and any(p.investor.id == card.investor.id for p in item.participants))
        observations.sort(key=lambda item: item[0], reverse=True)
        # Prefer the latest dated activity observation, and retain the score
        # component's evidence if it carries a different aggregate reference.
        activity_refs = (observations[0][1][:1] if observations else []) + activity.sources[:1]
    return _unique_sources(primary + activity_refs + shared[1:]
                           + (connection.sources if connection else []))


def _eur(value: Decimal) -> str:
    return f"EUR {value:,.2f}".removesuffix(".00")


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def _selected(result: RunResult, request: ChatRequest) -> list[CandidateCard]:
    query = f" {_normalize(request.message)} "
    named = [card for card in result.candidates if f" {_normalize(card.investor.name)} " in query]
    if named:
        return named
    if request.selected_investor_id:
        return [card for card in result.candidates if card.investor.id == request.selected_investor_id]
    return result.candidates[:1]


def _summary(card: CandidateCard) -> str:
    rounds = "round" if card.shared_round_count == 1 else "rounds"
    companies = "company" if card.shared_company_count == 1 else "companies"
    text = (f"{card.investor.name}: {card.fit_score}/100 introduction priority, "
            f"{round(card.profile_completeness * 100)}% profile completeness. "
            f"Cala records {card.shared_round_count} shared {rounds} across "
            f"{card.shared_company_count} {companies} with the confirmed lead.")
    if card.recent_rounds_12m is not None:
        text += f" {card.recent_rounds_12m} investment rounds were observed in the last 12 months."
    else:
        text += " Recent investment activity is unknown in this snapshot."
    return text


def answer_result(result: RunResult, request: ChatRequest) -> ChatResponse:
    """Use exactly the stored fields, keeping factual sources and unknowns intact."""
    if result.state == "no_open_allocation":
        return ChatResponse(message="There is no open allocation: firm commitments meet or exceed the round target. "
                            "No co-investor search was performed. Adjust the founder profile if your round changes.",
                            citations=[], candidate_ids=[], model_used=False)
    if not result.candidates:
        if result.state == "failed":
            message = "The search could not finish. A failed retrieval does not establish that no co-investors exist. "
            message += "You can adjust the profile or rerun the search; I cannot recommend an unsupported investor."
        else:
            message = (f"This {result.lookback_months}-month search has no completed, eligible lead-connected cards. "
                       f"It recorded {len(result.exclusions)} exclusions and {len(result.incomplete)} incomplete profiles. "
                       "An incomplete profile is not a rejection. Review search coverage and exclusions or rerun.")
        return ChatResponse(message=message, citations=[], candidate_ids=[], model_used=False)

    query = f" {_normalize(request.message)} "
    chosen = _selected(result, request)
    citations: list[Source] = []
    citation_groups: list[list[Source]] = []
    if any(word in query for word in ("exclude", "excluded", "do not contact", "declined")):
        if result.exclusions:
            message = "Recorded exclusions:\n" + "\n".join(
                f"- {item.investor.name}: {item.reason}" for item in result.exclusions)
            citations = [source for item in result.exclusions for source in item.sources]
        else:
            message = "No investors were excluded in this run. Advisory considerations do not automatically exclude anyone."
        message += "\nUse the do-not-contact and rerun action to change your founder exclusions. Chat does not change the shortlist."
        chosen = []
    elif any(word in query for word in ("draft", "introduction", "introduce", "follow up", "request", "write")):
        combined = any(word in query for word in ("combined", "all", "top three", "top 3"))
        if combined and result.combined_draft:
            chosen = result.candidates[:3]
            message = "Saved combined request from this run:\n\n" + result.combined_draft
        else:
            chosen = chosen[:1]
            message = "Saved request from this run:\n\n" + chosen[0].draft
        citation_groups = [_shared_sources(card) for card in chosen]
    elif any(word in query for word in ("compare", "versus", "difference", " vs ")):
        chosen = chosen if len(chosen) > 1 else result.candidates[:2]
        message = "Comparison of recorded cards:\n\n" + "\n\n".join(_summary(card) for card in chosen)
        message += "\n\nPriority is a heuristic, not participation probability. Unknown fields are not evidence of poor fit."
        citation_groups = [_summary_sources(card, result.confirmed_at.date()) for card in chosen]
    elif any(word in query for word in ("ticket", "cheque", "check size", "minimum", "participation size")):
        chosen = chosen[:1]
        card = chosen[0]
        if card.profile.ticket_criteria:
            ranges = []
            for ticket in card.profile.ticket_criteria:
                minimum = _eur(ticket.minimum_eur) if ticket.minimum_eur is not None else "unknown"
                maximum = _eur(ticket.maximum_eur) if ticket.maximum_eur is not None else "unknown"
                applicability = "currently applicable" if ticket.currently_applicable else "current applicability unconfirmed"
                ranges.append(f"- {ticket.kind.replace('_', ' ')}: {minimum} to {maximum}; {applicability}.")
                citations.extend(ticket.sources)
            message = f"Recorded participation criteria for {card.investor.name}:\n" + "\n".join(ranges)
        else:
            message = f"{card.investor.name}'s investor-level ticket criteria are unknown in this snapshot."
        message += " A company's total funding round is not the investor's check; ask the lead to confirm current participation size."
    elif any(word in query for word in ("overlap", "competitor", "competition", "compete", "consideration", "question")):
        chosen = chosen[:1]
        card = chosen[0]
        considerations = card.considerations
        if any(word in query for word in ("overlap", "competitor", "competition", "compete")):
            considerations = [item for item in considerations if item.code in ("portfolio_overlap", "founder_competitor")]
        if considerations:
            message = "Recorded considerations:\n" + "\n".join(f"- {item.basis}: {item.text}" for item in considerations)
            citations.extend(source for item in considerations for source in item.sources)
        else:
            message = f"No matching consideration is recorded for {card.investor.name}. That does not establish that no overlap or other issue exists."
        message += " Sector similarity alone is an expertise signal, not proof of competition."
    elif any(word in query for word in ("complete", "completeness", "unknown", "missing", "score", "rank", "why", "priority", "priorit", "top", "shortlist", "who should", "start with")):
        if any(word in query for word in ("top", "shortlist", "who should", "start with")) and not request.selected_investor_id:
            chosen = result.candidates[:3]
        message = "Recorded shortlist evidence:\n\n" + "\n\n".join(_summary(card) for card in chosen)
        citation_groups = [_summary_sources(card, result.confirmed_at.date()) for card in chosen]
        if len(chosen) == 1:
            unknown = [component.name.replace("_", " ") for component in chosen[0].components if not component.known]
            if unknown:
                message += "\n\nUnknown scoring fields: " + ", ".join(unknown) + "."
            message += "\n\nUnknown components keep a neutral value of 0.5. Completeness measures available fields, not reliability or investment likelihood."
            if chosen[0].considerations:
                message += "\n\nQuestions to confirm:\n" + "\n".join(
                    f"- {item.basis}: {item.text}" for item in chosen[0].considerations[:3])
                citation_groups.extend(item.sources for item in chosen[0].considerations[:3])
    elif any(word in query for word in ("graph", "connect", "shared", "relationship", "path", "network")):
        chosen = chosen[:1]
        card = chosen[0]
        lead_name = result.lead.name if result.lead else "the confirmed lead"
        deals = "\n".join(f"- {deal.company.name}: funding-event ID {deal.id}" for deal in card.shared_deals)
        message = (f"The public connection between {lead_name} and {card.investor.name} comes from participation "
                   f"in the same identified funding event, not just investments in the same company:\n{deals}\n\n"
                   "The founder-to-lead introduction route is founder-supplied context. Shared investments do not prove a personal relationship or an available introduction.")
        citation_groups = [_shared_sources(card)]
    elif any(word in query for word in ("interest", "guarantee", "will they", "join", "commit")):
        chosen = chosen[:1]
        card = chosen[0]
        message = (f"This run does not establish {card.investor.name}'s current investment interest or a guaranteed participation. "
                   "Historical shared investments provide introduction context. Ask your lead to confirm whether approaching them is appropriate.")
        if card.statuses:
            message += "\nFounder-supplied statuses: " + ", ".join(status.value.replace("_", " ") for status in card.statuses) + "."
        citation_groups = [_shared_sources(card)]
    else:
        message = ("I can help explain this saved search: ask why an investor is shortlisted, compare cards, "
                   "show an introduction request, explain a graph connection, or review unknowns and considerations. "
                   "Select an investor in the graph or shortlist to focus a question. Replies use the recorded result; "
                   "this assistant does not run new research or change your search.")
        chosen = []
    citations = _balanced_sources([citations, *citation_groups])
    if result.state == "partial":
        message += "\n\nCoverage is partial; this is available run evidence, not an exhaustive investment record."
    return ChatResponse(message=message[:6000], citations=citations,
                        candidate_ids=[card.investor.id for card in chosen], model_used=False)


class ResultChat:
    """No outbound destinations: questions and history stay in the local API."""

    async def reply(self, result: RunResult, request: ChatRequest) -> ChatResponse:
        if request.selected_investor_id is not None and request.selected_investor_id not in {
            card.investor.id for card in result.candidates
        }:
            raise ValueError("Selected investor does not belong to this shortlist")
        return answer_result(result, request)
