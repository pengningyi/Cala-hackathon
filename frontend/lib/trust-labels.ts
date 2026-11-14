import type { CandidateOrigin, RecommendedAction, TrustEdgeType, TrustHop } from "./types";

export const ACTION_LABELS: Record<RecommendedAction, string> = {
  reach_out_directly: "Reach out directly", reach_out_via_contact: "Reach out via contact", ask_lead_intro: "Ask lead for intro",
  advisor_angel: "Talk as advisor / angel", skip: "Low priority",
};
export const EDGE_LABELS: Record<TrustEdgeType, string> = {
  worked_together: "Overlapping roles", board: "Board overlap", co_invested: "Co-invested",
  person_at_fund: "Current role at fund", led: "Led your round", same_school: "Same school",
  invested_in: "Invested in", founded: "Founded",
};
export const ORIGIN_LABELS: Record<CandidateOrigin, string> = {
  lead_network: "Lead's co-investor", existing_investor: "Your investor", previous_company_backer: "Backed founder's previous company",
};

/** A readable sentence for one hop, e.g. "Kibo Ventures invested in Cala AI". */
export function hopText(hop: TrustHop): string {
  const org = hop.org ? ` at ${hop.org.name}` : "";
  switch (hop.edge_type) {
    case "invested_in": {
      // Paths run from your side to the investor, so the investor is usually the "to" end.
      const company = hop.org?.name || hop.from_name;
      const investor = hop.org && hop.to_name === hop.org.name ? hop.from_name : hop.to_name;
      return `${investor} invested in ${company}`;
    }
    case "founded": return `${hop.from_name} founded ${hop.org?.name || hop.to_name}`;
    case "co_invested": return `${hop.from_name} co-invested with ${hop.to_name}`;
    case "led": return `${hop.from_name} and ${hop.to_name}: lead of your round`;
    case "person_at_fund": return `${hop.from_name} works at ${hop.to_name}`;
    default: return `${hop.from_name} → ${hop.to_name} · ${EDGE_LABELS[hop.edge_type]}${org}`;
  }
}

/** "1 Mar 2024", "2014 – 2017" style ranges; a single date when both ends match. */
export function hopDates(hop: TrustHop, format: (value?: string | null) => string): string {
  if (!hop.since && !hop.until) return "";
  if (hop.since && hop.until && hop.since === hop.until) return ` · ${format(hop.since)}`;
  return ` · ${hop.since ? format(hop.since) : "date unknown"} – ${hop.until ? format(hop.until) : "present"}`;
}
