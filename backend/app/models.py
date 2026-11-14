from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(Model):
    url: HttpUrl
    retrieved_at: datetime
    published_at: date | None = None
    label: str = "Cala"


class EntityMetadataProperty(Model):
    name: str
    value: Any
    sources: list[Source] = Field(default_factory=list)


class Entity(Model):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["vc", "angel", "corporate_vc", "company", "unknown"] = "unknown"
    country: str | None = None
    website: HttpUrl | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list, description="Only Cala-identified aliases or vehicles of the same investment organization. Never general graph neighbors.")
    sources: list[Source] = Field(default_factory=list)
    entity_type: str | None = None
    headquarters_address: str | None = None
    registered_address: str | None = None
    country_basis: str | None = None
    country_sources: list[Source] = Field(default_factory=list)
    kind_basis: str | None = None
    kind_sources: list[Source] = Field(default_factory=list)
    metadata: list[EntityMetadataProperty] = Field(default_factory=list)
    metadata_status: Literal["not_requested", "retrieved", "unavailable"] = "not_requested"
    metadata_retrieved_at: datetime | None = None


class Startup(Model):
    cala_entity_id: str | None = None
    company_name: str = Field(min_length=1, max_length=200)
    website: HttpUrl | None = None
    product_description: str = Field(default="", max_length=4000)
    sector: str = Field(min_length=1, max_length=200)
    target_customer: str = Field(default="", max_length=2000)
    problem_solved: str = Field(default="", max_length=2000)
    country: str = Field(default="", max_length=100)
    known_competitors: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("website", mode="before")
    @classmethod
    def website_with_scheme(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return (value if "://" in value else "https://" + value) if value else None
        return value


class RoundInput(Model):
    stage: Literal["pre_seed", "seed", "series_a", "series_b"]
    target_eur: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    firm_commitments_eur: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    conditional_interest_eur: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    lead_status: Literal["signed", "firm_verbal", "conditional", "unconfirmed"] = "unconfirmed"
    lead_ticket_eur: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)

    @model_validator(mode="after")
    def validate_ticket(self):
        if self.lead_status in ("signed", "firm_verbal") and self.lead_ticket_eur is not None:
            if self.lead_ticket_eur > self.firm_commitments_eur:
                raise ValueError("Firm lead ticket cannot exceed total firm commitments")
        return self

    @property
    def remaining_eur(self) -> Decimal:
        return max(Decimal(0), self.target_eur - self.firm_commitments_eur)


class InvestorStatus(StrEnum):
    EXISTING_SHAREHOLDER = "existing_shareholder"
    FIRM_COMMITTED = "firm_committed"
    CONDITIONAL_INTEREST = "conditional_interest"
    ALREADY_APPROACHED = "already_approached"
    IN_CONVERSATION = "in_conversation"
    DECLINED = "declined"
    DO_NOT_CONTACT = "do_not_contact"


class StatusEntry(Model):
    investor_id: str | None = None
    investor_name: str | None = None
    statuses: list[InvestorStatus] = Field(min_length=1)

    @model_validator(mode="after")
    def require_identity(self):
        if not (self.investor_id or self.investor_name):
            raise ValueError("Provide investor_id or investor_name")
        return self


class Preferences(Model):
    include_angels: bool = True
    include_corporate_vcs: bool = True
    investor_countries: list[str] = Field(default_factory=list)


class FounderInput(Model):
    founder_name: str | None = Field(default=None, max_length=200)
    startup: Startup
    round: RoundInput
    lead_name: str = Field(default="", max_length=200)
    excluded_investors: list[str] = Field(default_factory=list, max_length=100)
    investor_statuses: list[StatusEntry] = Field(default_factory=list, max_length=100)
    preferences: Preferences = Field(default_factory=Preferences)


class ResolveRequest(Model):
    profile: FounderInput
    lookup_startup: bool = False


class DetectedLead(Model):
    lead: Entity
    company_id: str | None = None
    event_id: str | None = None
    stage: str | None = None
    event_date: date | None = None
    event_date_precision: str | None = None
    sources: list[Source] = Field(default_factory=list)


class Resolution(Model):
    id: str
    profile: FounderInput
    lead_matches: list[Entity]
    startup_matches: list[Entity] = Field(default_factory=list)
    resolver_ms: float
    created_at: datetime
    expires_at: datetime
    no_open_allocation: bool = False
    notices: list[str] = Field(default_factory=list)
    detected_lead: DetectedLead | None = None


class StartRunRequest(Model):
    resolution_id: str
    lead_id: str | None = None
    profile_confirmed: Literal[True]
    profile: FounderInput


class Participation(Model):
    investor: Entity
    is_lead: bool | None = None


class FundingRound(Model):
    id: str = Field(min_length=1)
    company: Entity
    event_date: date | None = None
    event_date_precision: str | None = None
    stage: str | None = None
    sector: str | None = None
    company_country: str | None = None
    # A funding-event total is never an investor's check.
    total_amount_eur: Decimal | None = Field(default=None, ge=0)
    participants: list[Participation] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class TicketCriterion(Model):
    kind: Literal["published_initial_range", "published_minimum_initial", "historical_typical", "historical_range"]
    minimum_eur: Decimal | None = Field(default=None, ge=0)
    maximum_eur: Decimal | None = Field(default=None, ge=0)
    currently_applicable: bool = False
    sources: list[Source] = Field(default_factory=list)


class Mandate(Model):
    allowed_countries: list[str] = Field(default_factory=list)
    allowed_stages: list[str] = Field(default_factory=list)
    currently_applicable: bool = False
    sources: list[Source] = Field(default_factory=list)


class Assessment(Model):
    company_id: str
    sector_fit: Literal["same", "adjacent", "unrelated", "unknown"] = "unknown"
    overlap: Literal["adjacent_business", "possible_overlap", "direct_substitute", "none", "unknown"] = "unknown"
    rationale: str
    label: Literal["LLM assessment"] = "LLM assessment"
    sources: list[Source] = Field(default_factory=list)


class InvestmentObservation(Model):
    """An identified investment event whose issuer profile may not be retrieved."""
    id: str = Field(min_length=1)
    event_date: date | None = None
    event_date_precision: str | None = None
    stage: str | None = None
    is_lead: bool | None = None
    sources: list[Source] = Field(default_factory=list)


class InvestorProfile(Model):
    investor: Entity
    rounds: list[FundingRound] = Field(default_factory=list)
    observations: list[InvestmentObservation] = Field(default_factory=list)
    # True only if Cala says the requested 24-month observation window is complete.
    activity_window_complete: bool = False
    ticket_criteria: list[TicketCriterion] = Field(default_factory=list)
    mandates: list[Mandate] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class GraphSnapshot(Model):
    rounds: list[FundingRound] = Field(default_factory=list)
    complete: bool = False
    lead: Entity | None = None


class ScoreComponent(Model):
    name: str
    weight: int
    value: float = Field(ge=0, le=1)
    known: bool
    numerator: float | None = None
    denominator: float | None = None
    explanation: str
    sources: list[Source] = Field(default_factory=list)


class Consideration(Model):
    code: str
    text: str
    basis: Literal["Cala fact", "LLM assessment", "Founder supplied"]
    sources: list[Source] = Field(default_factory=list)


class Exclusion(Model):
    investor: Entity
    code: str
    reason: str
    sources: list[Source] = Field(default_factory=list)


class InvestorFlag(Model):
    kind: str
    severity: Literal["red", "grey", "amber"]
    reason: str
    company: str | None = None
    sources: list[Source] = Field(default_factory=list)


class AllocationItem(Model):
    investor_id: str | None = None
    name: str
    amount_eur: Decimal = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    basis: str


class CandidateCard(Model):
    investor: Entity
    fit_score: int
    profile_completeness: float
    explanation: str
    shared_round_count: int
    shared_company_count: int
    shared_deals: list[FundingRound]
    recent_rounds_12m: int | None
    most_recent_investment: date | None
    components: list[ScoreComponent]
    considerations: list[Consideration]
    statuses: list[InvestorStatus]
    profile: InvestorProfile
    sources: list[Source]
    action: Literal["introduction", "follow_up"]
    draft: str
    review_status: Literal["short", "flagged"] = "short"
    flags: list[InvestorFlag] = Field(default_factory=list)
    estimated_ticket_eur: Decimal | None = Field(default=None, ge=0)
    ticket_estimate_basis: str | None = None


class ProgressEvent(Model):
    id: int
    type: str
    at: datetime
    step: str
    message: str
    tool: str | None = None
    query: dict[str, Any] | None = None
    count: int | None = None
    cached: bool | None = None


class RunResult(Model):
    run_id: str
    state: Literal["completed", "partial", "no_match", "no_open_allocation", "failed"]
    profile: FounderInput
    lead: Entity | None
    confirmed_at: datetime
    finished_at: datetime
    remaining_allocation_eur: Decimal
    lookback_months: int
    candidates: list[CandidateCard]
    exclusions: list[Exclusion]
    incomplete: list[Entity]
    counts: dict[str, int]
    notices: list[str]
    combined_draft: str
    processing_ms: float
    cala_calls: int
    cache_hits: int
    retrieval_timestamps: list[datetime]
    scoring_version: str = "main-1fed306"
    allocation: list[AllocationItem] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class RunStatus(Model):
    id: str
    state: Literal["running", "completed", "partial", "no_match", "no_open_allocation", "failed"]
    confirmed_at: datetime
    event_count: int
    result_url: str
    events_url: str


class ChatMessage(Model):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(Model):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=8)
    selected_investor_id: str | None = Field(default=None, max_length=300)


class ChatResponse(Model):
    message: str = Field(min_length=1, max_length=6000)
    citations: list[Source] = Field(default_factory=list, max_length=16)
    candidate_ids: list[str] = Field(default_factory=list, max_length=5)
    model_used: bool
