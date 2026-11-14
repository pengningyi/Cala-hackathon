import type { ReactNode } from "react";
import type { CandidateCard, RunResult, ScoreComponent, Source } from "../lib/types";
import { ACTION_LABELS } from "../lib/trust-labels";

/** The single rule set (backend/app/rules.py). Each rule met earns one point. */
export const RULES: Array<{ id: string; name: string; group: "Connection" | "Activity" | "Fit" | "Risk"; metWhen: string }> = [
  { id: "lead_coinvestor", name: "Lead co-investor", group: "Connection", metWhen: "At least one shared round with your lead" },
  { id: "repeat_partner", name: "Repeat partner", group: "Connection", metWhen: "Shared rounds with your lead in two or more companies" },
  { id: "recent_partner", name: "Recent partner", group: "Connection", metWhen: "A shared round with your lead in the last 24 months" },
  { id: "your_investor", name: "Your investor", group: "Connection", metWhen: "Invested in one of your company's own rounds" },
  { id: "backed_your_founder", name: "Backed your founder", group: "Connection", metWhen: "Backed a company one of your founders previously founded" },
  { id: "warm_path", name: "Warm path", group: "Connection", metWhen: "A sourced chain of professional connections links you to the investor" },
  { id: "lead_closed_path", name: "Lead-closed path", group: "Connection", metWhen: "A sourced connection chain that also runs through your lead" },
  { id: "recently_active", name: "Recently active", group: "Activity", metWhen: "Invested in the last 12 months" },
  { id: "stage_match", name: "Stage match", group: "Fit", metWhen: "At least 25% of its rounds with a known stage are at your stage" },
  { id: "sector_match", name: "Sector match", group: "Fit", metWhen: "Backed a company in your sector" },
  { id: "country_match", name: "Country match", group: "Fit", metWhen: "Backed a company headquartered in your country" },
  { id: "no_competitor", name: "No competitor", group: "Risk", metWhen: "Has not backed a competitor you named" },
];
const GROUPS = ["Connection", "Activity", "Fit", "Risk"] as const;
const RULE_BY_ID = new Map(RULES.map(rule => [rule.id, rule]));

export const rulesMet = (card: CandidateCard) => card.components.filter(item => item.known && item.value >= 1).length;
const isRuleCard = (card: CandidateCard) => card.components.some(item => RULE_BY_ID.has(item.name));

function status(item: ScoreComponent): "met" | "not-met" | "unchecked" {
  return !item.known ? "unchecked" : item.value >= 1 ? "met" : "not-met";
}

/** Per-investor checklist: each rule is met, not met, or not checked (no data). */
export function RuleChecklist({ card, renderSources }: { card: CandidateCard; renderSources: (sources: Source[]) => ReactNode }) {
  if (!isRuleCard(card)) return null;
  return <div className="rule-checklist">
    <div className="rule-total"><strong>{card.fit_score} rules met</strong><small>One point per rule met. Rules Cala has no data for are marked "Not checked" and earn nothing.</small></div>
    {GROUPS.map(group => {
      const items = card.components.filter(item => RULE_BY_ID.get(item.name)?.group === group);
      if (!items.length) return null;
      return <section key={group} className="rule-group"><h5>{group}<span>{items.filter(item => status(item) === "met").length} met</span></h5>
        <ul>{items.map(item => {
          const rule = RULE_BY_ID.get(item.name)!;
          const state = status(item);
          return <li key={item.name} className={`rule-row rule-${state}`}>
            <span className="rule-mark" aria-hidden="true">{state === "met" ? "✓" : state === "not-met" ? "✗" : "–"}</span>
            <div><strong title={rule.metWhen}>{rule.name}</strong>
              <span className="rule-state">{state === "met" ? "Met" : state === "not-met" ? "Not met" : "Not checked"}</span>
              <p>{item.explanation.replace(/^Not checked: /, "")}</p>{state !== "unchecked" && renderSources(item.sources)}</div>
          </li>;
        })}</ul></section>;
    })}
  </div>;
}

/** The fixed action ladder, with this investor's step highlighted. */
export function ActionLadder({ card }: { card: CandidateCard }) {
  const steps: Array<{ action: NonNullable<CandidateCard["recommended_action"]>; when: string }> = [
    { action: "reach_out_directly", when: "Your investor" },
    { action: "reach_out_via_contact", when: "Warm path" },
    { action: "ask_lead_intro", when: "Lead co-investor" },
    { action: "skip", when: "None of the above" },
  ];
  return <ol className="action-ladder" aria-label="How the recommended action is chosen">
    {steps.map(step => <li key={step.action} className={card.recommended_action === step.action ? "is-current" : ""}>
      <span>{step.when}</span><strong>{ACTION_LABELS[step.action]}</strong></li>)}
  </ol>;
}

/** Results-level guide: the rules, how they rank investors, and how they pick the action. */
export function RankingGuide({ result }: { result: RunResult }) {
  const cards = result.candidates;
  if (!cards.length || !cards.some(isRuleCard)) return null;
  const metCount = (id: string) => cards.filter(card => card.components.some(item => item.name === id && item.known && item.value >= 1)).length;
  return <details className="ranking-guide"><summary>How this shortlist is ranked<span className="mono">{result.scoring_version}</span></summary>
    <p>Each investor is checked against {RULES.length} rules. Every rule met earns one point; the score is the number of rules met.</p>
    <table><thead><tr><th>Rule</th><th>Met when</th><th>Met by</th></tr></thead><tbody>
      {RULES.map(rule => <tr key={rule.id}><td>{rule.name}<span className="rule-group-tag">{rule.group}</span></td><td>{rule.metWhen}</td>
        <td className="mono">{metCount(rule.id)}</td></tr>)}</tbody></table>
    <p><strong>Order:</strong> most rules met, then most Connection rules met, then the most recent sourced round, then name.</p>
    <p><strong>Action:</strong> Your investor → reach out directly; Warm path → reach out via the contact; Lead co-investor → ask your lead for an intro; otherwise low priority.</p>
  </details>;
}
