"""Ranking and recommended action, both derived from the rule set in rules.py.

There is one set of rules. An investor's score is the number of rules met;
ranking sorts by that score, then by Connection rules met, then by the most
recent sourced evidence, then by name and ID for a stable order. The action is
a fixed ladder over the same rules, with no relative cutoffs.
"""
from __future__ import annotations

from datetime import date

from . import rules as rule_book
from .models import CandidateCard

SCORING_VERSION = "rules-2026-11"


def recommended_action(card: CandidateCard) -> tuple[str, object | None]:
    """Your investor -> reach out directly; warm path -> via the contact;
    lead co-investor -> ask your lead; otherwise low priority."""
    if rule_book.is_met(card, "your_investor") or "existing_investor" in card.origins:
        return "reach_out_directly", None
    if rule_book.is_met(card, "warm_path"):
        loop = next((loop for loop in card.loops if loop.contact is not None), None)
        return "reach_out_via_contact", loop.contact if loop else None
    if rule_book.is_met(card, "lead_coinvestor"):
        return "ask_lead_intro", None
    return "skip", None


def _latest_evidence(card: CandidateCard) -> date:
    dates = [r.event_date for r in card.shared_deals + card.origin_rounds if r.event_date]
    return max(dates, default=date.min)


def order_key(card: CandidateCard):
    from .scoring import normalized_name
    return (-card.fit_score, -rule_book.connection_met(card), -_latest_evidence(card).toordinal(),
            normalized_name(card.investor.name), card.investor.id)


def rank(cards: list[CandidateCard]) -> list[CandidateCard]:
    return sorted(cards, key=order_key)
