/** Wire types for backend/app/models.py. Monetary decimals remain exact strings. */
export type DecimalString = string;
export type InvestorKind = "vc" | "angel" | "corporate_vc" | "company" | "unknown";
export type InvestorStatus =
  | "existing_shareholder"
  | "firm_committed"
  | "conditional_interest"
  | "already_approached"
  | "in_conversation"
  | "declined"
  | "do_not_contact";

export interface Source {
  url: string;
  retrieved_at: string;
  published_at: string | null;
  label: string;
}

export interface EntityMetadataProperty {
  name: string;
  value: unknown;
  sources: Source[];
}

export interface Entity {
  id: string;
  name: string;
  kind: InvestorKind;
  country: string | null;
  website: string | null;
  description: string | null;
  aliases: string[];
  related_entity_ids: string[];
  sources: Source[];
  entity_type?: string | null;
  headquarters_address?: string | null;
  office_address?: string | null;
  registered_address?: string | null;
  country_basis?: string | null;
  country_sources?: Source[];
  kind_basis?: string | null;
  kind_sources?: Source[];
  metadata?: EntityMetadataProperty[];
  metadata_status?: "not_requested" | "retrieved" | "unavailable";
  metadata_retrieved_at?: string | null;
}

export interface Startup {
  cala_entity_id?: string | null;
  company_name: string;
  website?: string | null;
  product_description: string;
  sector: string;
  target_customer: string;
  problem_solved: string;
  country: string;
  known_competitors: string[];
}

export interface RoundInput {
  stage: "pre_seed" | "seed" | "series_a" | "series_b";
  target_eur: DecimalString;
  firm_commitments_eur: DecimalString;
  conditional_interest_eur?: DecimalString;
  lead_status: "signed" | "firm_verbal" | "conditional" | "unconfirmed";
  lead_ticket_eur?: DecimalString | null;
}

export interface StatusEntry {
  investor_id?: string | null;
  investor_name?: string | null;
  statuses: InvestorStatus[];
}

export interface Preferences {
  include_angels: boolean;
  include_corporate_vcs: boolean;
  investor_countries: string[];
}

export interface FounderInput {
  founder_name?: string | null;
  startup: Startup;
  round: RoundInput;
  lead_name: string;
  investor_statuses: StatusEntry[];
  preferences: Preferences;
  excluded_investors?: string[];
  /** Optional partner at the lead fund who owns this deal. */
  lead_partner_name?: string | null;
  founder_person_ids?: string[];
  /** Loops the founder marked "I know this person". */
  confirmed_loop_ids?: string[];
}

export interface ResolveRequest {
  profile: FounderInput;
  lookup_startup: boolean;
}

export interface CompanyMatches {
  id: string;
  startup_matches: Entity[];
  lead_matches: Entity[];
  resolver_ms: number;
  created_at: string;
  expires_at: string;
  notices: string[];
}

export interface OnboardingConfirmation {
  resolution_id: string;
  startup: Entity;
  lead: Entity;
  prefill: { company_name: string; website: string; sector: string; country: string };
  prefill_sources: Record<string, Source[]>;
  notices: string[];
}

export interface Resolution {
  id: string;
  profile: FounderInput;
  lead_matches: Entity[];
  startup_matches: Entity[];
  resolver_ms: number;
  created_at: string;
  expires_at: string;
  no_open_allocation: boolean;
  notices: string[];
  detected_lead?: {
    lead: Entity;
    company_id: string | null;
    event_id: string | null;
    stage: string | null;
    event_date: string | null;
    event_date_precision?: string | null;
    sources: Source[];
  } | null;
}

export interface StartRunRequest {
  resolution_id: string;
  lead_id: string | null;
  profile_confirmed: true;
  profile: FounderInput;
}

export interface Participation {
  investor: Entity;
  is_lead: boolean | null;
}

export interface FundingRound {
  id: string;
  company: Entity;
  event_date: string | null;
  event_date_precision: string | null;
  stage: string | null;
  sector: string | null;
  company_country: string | null;
  /** Total funding-event amount. This is never an individual investor ticket. */
  total_amount_eur: DecimalString | null;
  participants: Participation[];
  sources: Source[];
}

export interface TicketCriterion {
  kind:
    | "published_initial_range"
    | "published_minimum_initial"
    | "historical_typical"
    | "historical_range";
  minimum_eur: DecimalString | null;
  maximum_eur: DecimalString | null;
  currently_applicable: boolean;
  sources: Source[];
}

export interface Mandate {
  allowed_countries: string[];
  allowed_stages: string[];
  currently_applicable: boolean;
  sources: Source[];
}

export interface Assessment {
  company_id: string;
  sector_fit: "same" | "adjacent" | "unrelated" | "unknown";
  overlap: "adjacent_business" | "possible_overlap" | "direct_substitute" | "none" | "unknown";
  rationale: string;
  label: "LLM assessment";
  sources: Source[];
}

export interface InvestmentObservation {
  id: string;
  event_date: string | null;
  event_date_precision: string | null;
  stage: string | null;
  is_lead: boolean | null;
  sources: Source[];
}

export interface InvestorProfile {
  investor: Entity;
  rounds: FundingRound[];
  observations: InvestmentObservation[];
  activity_window_complete: boolean;
  ticket_criteria: TicketCriterion[];
  mandates: Mandate[];
  assessments: Assessment[];
  sources: Source[];
}

export interface GraphSnapshot {
  lead?: Entity | null;
  rounds: FundingRound[];
  complete: boolean;
}

export interface ScoreComponent {
  name: string;
  weight: number;
  value: number;
  known: boolean;
  numerator: number | null;
  denominator: number | null;
  explanation: string;
  sources: Source[];
  /** Rule group (Connection, Activity, Fit, Risk) under rule scoring. */
  group?: string | null;
}

export interface Consideration {
  code: string;
  text: string;
  basis: "Cala fact" | "LLM assessment" | "Founder supplied";
  sources: Source[];
}

export interface Exclusion {
  investor: Entity;
  code: string;
  reason: string;
  sources: Source[];
}

export interface CandidateCard {
  investor: Entity;
  /** Under rule scoring: the number of rules met. Older results: a 0-100 score. */
  fit_score: number;
  /** Fraction from 0 to 1, independent of fit_score. */
  profile_completeness: number;
  explanation: string;
  shared_round_count: number;
  shared_company_count: number;
  shared_deals: FundingRound[];
  recent_rounds_12m: number | null;
  most_recent_investment: string | null;
  components: ScoreComponent[];
  considerations: Consideration[];
  statuses: InvestorStatus[];
  profile: InvestorProfile;
  sources: Source[];
  action: "introduction" | "follow_up";
  draft: string;
  review_status?: "short" | "flagged";
  flags?: Array<{
    kind: string;
    severity: "red" | "grey" | "amber";
    reason: string;
    company: string | null;
    sources: Source[];
  }>;
  estimated_ticket_eur?: DecimalString | null;
  ticket_estimate_basis?: string | null;
  /** Reachability (0-1), separate from fit_score; null when no people data exists. */
  trust?: number | null;
  loops?: TrustLoop[];
  recommended_action?: RecommendedAction;
  action_contact?: Entity | null;
  /** Why the investor is a candidate; a shared round with the lead is not required. */
  origins?: CandidateOrigin[];
  /** Rounds of your company or a founder's previous company that the investor joined. */
  origin_rounds?: FundingRound[];
}

export type TrustEdgeType = "worked_together" | "board" | "co_invested" | "person_at_fund" | "led" | "same_school"
  | "invested_in" | "founded";

export interface TrustHop {
  from_id: string;
  from_name: string;
  to_id: string;
  to_name: string;
  edge_type: TrustEdgeType;
  org: Entity | null;
  since: string | null;
  until: string | null;
  strength: number;
  sources: Source[];
}

export interface TrustLoop {
  id: string;
  kind: "known" | "one_contact" | "multi_contact" | "lead_loop" | "existing_investor" | "previous_company";
  contact: Entity | null;
  /** The loop also closes through the investor's co-investment with your lead. */
  closes_through_lead?: boolean;
  hops: TrustHop[];
  strength: number;
  confirmed: boolean;
  label: string;
}

export type RecommendedAction = "reach_out_directly" | "reach_out_via_contact" | "ask_lead_intro" | "advisor_angel" | "skip";
export type CandidateOrigin = "lead_network" | "existing_investor" | "previous_company_backer";

export interface AllocationItem {
  investor_id: string | null;
  name: string;
  amount_eur: DecimalString;
  percentage: number;
  basis: string;
}

export interface ProgressEvent {
  id: number;
  type: string;
  at: string;
  step: string;
  message: string;
  tool: string | null;
  query: Record<string, unknown> | null;
  count: number | null;
  cached: boolean | null;
}

export type ResultState = "completed" | "partial" | "no_match" | "no_open_allocation" | "failed";

export interface RunResult {
  run_id: string;
  state: ResultState;
  profile: FounderInput;
  lead: Entity | null;
  confirmed_at: string;
  finished_at: string;
  remaining_allocation_eur: DecimalString;
  lookback_months: number;
  candidates: CandidateCard[];
  exclusions: Exclusion[];
  incomplete: Entity[];
  counts: Record<string, number>;
  notices: string[];
  combined_draft: string;
  processing_ms: number;
  cala_calls: number;
  cache_hits: number;
  retrieval_timestamps: string[];
  scoring_version: string;
  allocation?: AllocationItem[];
  error: Record<string, unknown> | null;
}

export interface RunStatus {
  id: string;
  state: "running" | ResultState;
  confirmed_at: string;
  event_count: number;
  result_url: string;
  events_url: string;
}

export interface Health {
  status: "healthy";
  cala_configured: boolean;
  model_provider: "codex" | "openai_compatible" | "disabled";
  storage: "in_memory";
  api_version: string;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: unknown;
  };
}
