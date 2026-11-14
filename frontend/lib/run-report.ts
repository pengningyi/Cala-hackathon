import type { CandidateCard, RunResult } from "./types";
import { ACTION_LABELS, ORIGIN_LABELS } from "./trust-labels";

/** One completed chat message, as the results chat reports it. */
export interface ReportTurn {
  role: "user" | "assistant";
  content: string;
  candidateIds: string[];
}

export interface ReportAction {
  text: string;
  /** Where the action came from: the ranked shortlist, the chat, or the round itself. */
  source: "Shortlist" | "Chat" | "Round";
}

export interface ReportInvestor {
  id: string;
  rank: number;
  name: string;
  score: string;
  action: string;
  why: string;
  detail: string;
}

export interface ReportExchange {
  question: string;
  answer: string;
  investors: string[];
}

export interface RunReport {
  title: string;
  company: string;
  generatedAt: Date;
  summary: string;
  round: Array<{ label: string; value: string }>;
  actions: ReportAction[];
  investors: ReportInvestor[];
  exchanges: ReportExchange[];
  caveats: string[];
}

const MAX_ACTIONS = 7;
const MAX_EXCHANGES = 6;
const MAX_ANSWER = 320;
const ACTION_VERBS = /^(ask|reach|send|email|e-mail|message|follow|schedule|book|call|confirm|draft|introduce|contact|request|prepare|share|review|update|check|verify|prioriti[sz]e|start|lead with|mention|offer|set up|get|secure|line up|close|finali[sz]e|use|approach|target|focus|reply|write|warm|loop in|cc)\b/i;

const STAGE_LABELS: Record<string, string> = { pre_seed: "Pre-seed", seed: "Seed", series_a: "Series A", series_b: "Series B" };

export function money(value: string | null | undefined): string {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) return "Unknown";
  return new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(value));
}

function leadStatus(result: RunResult): string {
  const status = result.profile.round.lead_status;
  return status === "unconfirmed" ? "Unconfirmed" : status === "conditional" ? "Provisional" : status === "signed" ? "Signed" : "Firm verbal";
}

/** Plain text from chat markdown: no emphasis, links, code or evidence IDs such as "[s15]". */
export function plainText(markdown: string): string {
  return markdown
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[(\[]\s*s\d+(?:\s*[,;]\s*s\d+)*\s*[)\]]/g, "")
    .replace(/(?<![\w/.-])s\d+\b/g, "")
    .replace(/(\*\*|__|`)/g, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[ \t]+([.,;:!?])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function sentence(text: string): string {
  const clean = text.replace(/\s+/g, " ").replace(/[:;,\s]+$/, "").trim();
  if (!clean) return "";
  const capital = clean[0].toUpperCase() + clean.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

const LIST_ITEM = /^\s*(?:[-*+•]|\d+[.)])\s+(.*)$/;
const LABELLED = /^\s*(?:next steps?|action(?: item)?s?|recommendation|to do)\s*[:\-–]\s*(.+)$/i;

/** Imperative list items and "Next step:" lines in an assistant reply. */
export function extractActions(markdown: string): string[] {
  const found: string[] = [];
  for (const raw of markdown.split(/\r?\n/)) {
    const labelled = raw.replace(/\*\*/g, "").match(LABELLED);
    const item = raw.match(LIST_ITEM);
    const body = labelled?.[1] ?? item?.[1];
    if (!body) continue;
    let text = plainText(body);
    // "Kibo Ventures: ask your lead for an intro" -> "ask your lead for an intro"
    const prefixed = text.match(/^[^:]{1,40}:\s+(.+)$/);
    if (prefixed && ACTION_VERBS.test(prefixed[1])) text = prefixed[1];
    if (labelled || ACTION_VERBS.test(text)) found.push(sentence(text));
  }
  return found.filter(Boolean);
}

/** The reply without its action lines, shortened to a readable excerpt. */
function excerpt(markdown: string): string {
  const lines = markdown.split(/\r?\n/).filter(line => {
    const item = line.match(LIST_ITEM);
    if (LABELLED.test(line.replace(/\*\*/g, ""))) return false;
    return !(item && ACTION_VERBS.test(plainText(item[1])));
  });
  const text = plainText(lines.map(line => line.replace(LIST_ITEM, "$1")).join(" ")).replace(/\s+/g, " ").trim();
  if (text.length <= MAX_ANSWER) return text;
  const cut = text.slice(0, MAX_ANSWER);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? "), cut.lastIndexOf("! "));
  return end > MAX_ANSWER * 0.5 ? cut.slice(0, end + 1) : `${cut.slice(0, cut.lastIndexOf(" "))}…`;
}

function shortlistAction(card: CandidateCard, result: RunResult): string | null {
  const name = card.investor.name;
  const lead = result.lead?.name || result.profile.lead_name || "your lead";
  const contact = card.action_contact?.name;
  switch (card.recommended_action) {
    case "reach_out_directly": return card.action === "follow_up"
      ? `Follow up with ${name} directly about allocation in this round.` : `Reach out to ${name} directly.`;
    case "reach_out_via_contact": return contact ? `Ask ${contact} for a warm introduction to ${name}.` : `Find a mutual contact for an introduction to ${name}.`;
    case "ask_lead_intro": return `Ask ${lead} to introduce you to ${name}.`;
    case "advisor_angel": return `Approach ${contact || name} as a potential advisor or angel.`;
    case "skip": return null;
    default: return card.action === "follow_up" ? `Follow up with ${name}.` : `Request an introduction to ${name} through ${lead}.`;
  }
}

function key(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();
}

function pairExchanges(turns: ReportTurn[], names: Map<string, string>): ReportExchange[] {
  const exchanges: ReportExchange[] = [];
  for (let index = 0; index < turns.length; index += 1) {
    const turn = turns[index];
    if (turn.role !== "user") continue;
    const reply = turns[index + 1]?.role === "assistant" ? turns[index + 1] : null;
    if (!reply) continue;
    exchanges.push({ question: plainText(turn.content), answer: excerpt(reply.content),
      investors: [...new Set(reply.candidateIds)].map(id => names.get(id)).filter((name): name is string => Boolean(name)) });
    index += 1;
  }
  return exchanges.slice(-MAX_EXCHANGES);
}

/** Summary, next actions, ranked investors and chat highlights for one run. */
export function buildRunReport(result: RunResult, turns: ReportTurn[], now = new Date()): RunReport {
  const { profile } = result;
  const company = profile.startup.company_name || "Your company";
  const lead = result.lead?.name || profile.lead_name || "your lead";
  const rules = result.scoring_version.startsWith("rules-");
  const ready = result.candidates.filter(card => card.review_status !== "flagged");
  const flagged = result.candidates.filter(card => card.review_status === "flagged");
  const provisional = profile.round.lead_status === "conditional" || profile.round.lead_status === "unconfirmed";
  const names = new Map(result.candidates.map(card => [card.investor.id, card.investor.name]));

  // Summary
  const stage = STAGE_LABELS[profile.round.stage] || profile.round.stage;
  const parts = [`${company} is raising a ${money(profile.round.target_eur)} ${stage} round led by ${lead} (${leadStatus(result).toLowerCase()}), with ${money(profile.round.firm_commitments_eur)} firmly committed and ${money(result.remaining_allocation_eur)} still open.`];
  if (result.candidates.length) {
    const top = result.candidates.slice(0, 3).map(card => card.investor.name);
    parts.push(`The search profiled ${result.counts.profiled ?? 0} of ${result.counts.discovered ?? 0} discovered investors and shortlisted ${result.candidates.length}: ${ready.length} ready to contact${flagged.length ? ` and ${flagged.length} needing review` : ""}. The strongest options are ${listJoin(top)}.`);
  } else if (result.state === "no_open_allocation") {
    parts.push("Firm commitments already meet the target, so no co-investor search was needed.");
  } else {
    parts.push("No completed investor cards came out of this run.");
  }
  const asked = turns.filter(turn => turn.role === "user").length;
  if (asked) parts.push(`You asked the round assistant ${asked} ${asked === 1 ? "question" : "questions"}; the highlights and the actions it suggested are included below.`);

  // Actions: round blockers first, then the chat's suggestions, then the shortlist's top routes.
  const actions: ReportAction[] = [];
  const seen = new Set<string>();
  const add = (text: string | null, source: ReportAction["source"]) => {
    if (!text) return;
    const id = key(text);
    if (!id || seen.has(id)) return;
    seen.add(id); actions.push({ text, source });
  };
  if (provisional) add(`Confirm ${lead}'s lead commitment; introductions land better once it is firm.`, "Round");
  const chatActions = turns.filter(turn => turn.role === "assistant").flatMap(turn => extractActions(turn.content));
  chatActions.slice(-4).forEach(text => add(text, "Chat"));
  ready.slice(0, 3).forEach(card => add(shortlistAction(card, result), "Shortlist"));
  if (flagged.length) add(`Review the flags on ${listJoin(flagged.map(card => card.investor.name))} before contacting ${flagged.length === 1 ? "them" : "any of them"}.`, "Shortlist");
  if (result.combined_draft && ready.length > 1) add(`Send ${lead} the combined introduction request for your top ${Math.min(3, ready.length)} investors.`, "Shortlist");
  if (!result.candidates.length && result.state !== "no_open_allocation") add("Adjust your round profile (sector, countries, investor types) and rerun the search.", "Round");

  const investors = result.candidates.slice(0, 5).map((card, index): ReportInvestor => {
    const origins = (card.origins || []).filter(origin => origin !== "lead_network").map(origin => ORIGIN_LABELS[origin]);
    const detail = [
      `${card.shared_round_count} shared ${card.shared_round_count === 1 ? "round" : "rounds"} with ${lead}`,
      ...origins,
      card.action_contact ? `Contact: ${card.action_contact.name}` : "",
      card.estimated_ticket_eur ? `Est. ticket ${money(card.estimated_ticket_eur)}` : "",
      card.review_status === "flagged" ? "Needs review" : "",
    ].filter(Boolean).join(" · ");
    return { id: card.investor.id, rank: index + 1, name: card.investor.name,
      score: rules ? `${card.fit_score} ${card.fit_score === 1 ? "rule" : "rules"} met` : `${card.fit_score}/100`,
      action: card.recommended_action ? ACTION_LABELS[card.recommended_action] : card.action === "follow_up" ? "Follow up" : "Introduction",
      why: plainText(card.explanation), detail };
  });

  const caveats: string[] = [];
  if (result.state === "partial") caveats.push("Coverage is partial: some investor facts were unavailable and stay unknown.");
  if (provisional) caveats.push(`The lead commitment is ${leadStatus(result).toLowerCase()}; drafts say so explicitly.`);
  if (result.candidates.some(card => card.loops?.some(loop => !loop.confirmed))) caveats.push("Warm paths are based on public professional overlap; personal relationships are unconfirmed.");
  caveats.push(rules ? "Rules met is an introduction priority, not an investment probability." : "Scores are an introduction priority, not an investment probability.");
  if (turns.some(turn => turn.role === "assistant")) caveats.push("Conversation highlights are excerpts of the assistant's replies; check its cited sources before acting.");
  result.notices.forEach(notice => caveats.push(notice));

  return { title: `${company} round report`, company, generatedAt: now, summary: parts.join(" "),
    round: [
      { label: "Stage", value: stage },
      { label: "Target", value: money(profile.round.target_eur) },
      { label: "Open allocation", value: money(result.remaining_allocation_eur) },
      { label: "Lead", value: `${lead} · ${leadStatus(result)}` },
    ],
    actions: actions.slice(0, MAX_ACTIONS), investors, exchanges: pairExchanges(turns, names), caveats };
}

function listJoin(items: string[]): string {
  if (items.length < 2) return items[0] || "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export function reportDate(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function reportFilename(report: RunReport): string {
  const slug = report.company.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "round";
  const d = report.generatedAt;
  const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `${slug}-report-${iso}.md`;
}

export function reportMarkdown(report: RunReport): string {
  const lines = [`# ${report.title}`, "", `_Generated ${reportDate(report.generatedAt)}_`, "", "## Summary", "", report.summary, "",
    ...report.round.map(item => `- **${item.label}:** ${item.value}`), "", "## Key next actions", ""];
  if (report.actions.length) report.actions.forEach((action, index) => lines.push(`${index + 1}. ${action.text} _(${action.source})_`));
  else lines.push("No actions yet.");
  lines.push("", "## Top investors", "");
  if (report.investors.length) report.investors.forEach(investor => {
    lines.push(`${investor.rank}. **${investor.name}** — ${investor.score} · ${investor.action}`);
    if (investor.why) lines.push(`   ${investor.why}`);
    if (investor.detail) lines.push(`   _${investor.detail}_`);
  });
  else lines.push("No investors on this shortlist.");
  lines.push("", "## Conversation highlights", "");
  if (report.exchanges.length) report.exchanges.forEach(exchange => {
    lines.push(`**Q: ${exchange.question}**`, "", `A: ${exchange.answer}`);
    if (exchange.investors.length) lines.push("", `_Investors discussed: ${exchange.investors.join(", ")}_`);
    lines.push("");
  });
  else lines.push("No questions asked yet.", "");
  lines.push("## Caveats", "", ...report.caveats.map(caveat => `- ${caveat}`), "");
  return lines.join("\n");
}
