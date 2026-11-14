"use client";

import Image from "next/image";
import { cloneElement, isValidElement, useEffect, useId, useRef, useState, type FormEvent, type ReactElement, type ReactNode } from "react";
import {
  ArrowDown, ArrowLeft, ArrowRight, Building2, Check, CheckCheck, ChevronDown,
  CircleHelp, Clock3, Copy, ExternalLink, FileCheck2, GitBranch, Layers3,
  Link2, LoaderCircle, Network, Plus, Radio, RotateCcw, ShieldCheck, Sparkles, X,
} from "lucide-react";
import { ApiError, confirmCompanyMatches, findCompanyMatches, getHealth, getRunResult, startRun, subscribeRunEvents } from "../lib/api";
import type { CandidateCard, CompanyMatches, Entity, FounderInput, InvestorStatus, OnboardingConfirmation, ProgressEvent, RunResult, RunStatus, Source } from "../lib/types";
import ResultsWorkspace from "../components/results-workspace";
import { ORIGIN_LABELS, hopDates, hopText } from "../lib/trust-labels";
import { ActionLadder, RuleChecklist } from "../components/score-explainer";
import EntityMetadata, { EntityIdentity } from "../components/entity-metadata";
import { activeRunStage, RUN_STAGES } from "../lib/run-progress";

type Phase = "identify" | "matches" | "details" | "running" | "results";
type StatusRow = { investor_name: string; investor_id?: string; status: InvestorStatus };
type FormData = {
  founder_name: string; company_name: string; website: string; sector: string; country: string;
  known_competitors: string; stage: "" | "pre_seed" | "seed" | "series_a" | "series_b"; target_eur: string;
  firm_commitments_eur: string; lead_name: string; lead_partner_name: string; excluded_investors: string;
  lead_status: "signed" | "firm_verbal" | "conditional" | "unconfirmed";
  include_angels: boolean; include_corporate_vcs: boolean; investor_countries: string;
  statuses: StatusRow[];
};
const INITIAL: FormData = {
  founder_name: "", company_name: "Cala AI", website: "",
  sector: "", country: "", known_competitors: "", stage: "",
  target_eur: "", firm_commitments_eur: "", lead_name: "Lightspeed Venture Partners", lead_partner_name: "", excluded_investors: "",
  lead_status: "unconfirmed", include_angels: true,
  include_corporate_vcs: true, investor_countries: "", statuses: [],
};
const STATUS_LABELS: Record<InvestorStatus, string> = {
  existing_shareholder: "Existing shareholder", firm_committed: "Firm committed this round",
  conditional_interest: "Conditional interest", already_approached: "Already approached",
  in_conversation: "In conversation", declined: "Declined this round", do_not_contact: "Do not contact",
};
const EUR = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });
const money = (value: string | number | null | undefined) => value == null ? "Unknown" : EUR.format(Number(value));
const label = (value: string) => value.replaceAll("_", " ");
const dateLabel = (value?: string | null, precision?: string | null) => {
  if (!value) return "Date unknown";
  const options: Intl.DateTimeFormatOptions = precision === "year" ? { year: "numeric" }
    : precision === "month" ? { month: "short", year: "numeric" } : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat("en-GB", options).format(new Date(value.includes("T") ? value : `${value}T12:00:00`));
};
const errorMessage = (error: unknown) => {
  if (error instanceof ApiError && Array.isArray(error.details)) {
    const messages = error.details.flatMap((detail: unknown) => {
      if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") {
        return [detail.message.replace(/^Value error,\s*/i, "")];
      }
      return [];
    });
    if (messages.length) return [...new Set(messages)].slice(0, 3).join(". ");
  }
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
};
const lines = (value: string) => value.split(/[\n,]/).map(part => part.trim()).filter(Boolean);

/** Parse the main form's EUR amounts exactly, including its K/M shorthand. */
function euroAmount(value: string, title: string, positive = false): string {
  const text = value.trim().replace(/^(?:€|EUR)\s*/i, "").replace(/\s*EUR$/i, "").trim();
  const match = text.match(/^(\d+|\d{1,3}(?:,\d{3})+)(?:\.(\d{1,2}))?\s*([kmb])?$/i);
  if (!match) throw new Error(`${title} must be a non-negative EUR amount, such as 2,000,000 or 2M.`);
  const multiplier = { k: BigInt(1000), m: BigInt(1000000), b: BigInt(1000000000) }[match[3]?.toLowerCase() as "k" | "m" | "b"] || BigInt(1);
  const cents = (BigInt(match[1].replaceAll(",", "")) * BigInt(100) + BigInt((match[2] || "").padEnd(2, "0"))) * multiplier;
  if ((positive && cents === BigInt(0)) || cents >= BigInt(10) ** BigInt(16)) throw new Error(`${title} must be ${positive ? "positive and " : ""}less than EUR 100 trillion.`);
  return `${cents / BigInt(100)}.${String(cents % BigInt(100)).padStart(2, "0")}`;
}

function companyWebsite(value: string): string | null {
  if (!value.trim()) return null;
  try {
    const parsed = new URL(value.includes("://") ? value.trim() : `https://${value.trim()}`);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || !parsed.hostname) throw new Error();
    return parsed.href;
  } catch { throw new Error("Enter a valid company website, such as cala.ai or https://cala.ai."); }
}

function makeProfile(form: FormData): FounderInput {
  if (!form.stage) throw new Error("Choose the stage of your current round.");
  return {
    founder_name: form.founder_name || null,
    startup: { company_name: form.company_name, website: companyWebsite(form.website),
      product_description: "", sector: form.sector, target_customer: "", problem_solved: "",
      country: form.country, known_competitors: lines(form.known_competitors) },
    round: { stage: form.stage, target_eur: euroAmount(form.target_eur, "Round target", true),
      firm_commitments_eur: euroAmount(form.firm_commitments_eur, "Already committed"),
      conditional_interest_eur: "0", lead_status: form.lead_status, lead_ticket_eur: null },
    lead_name: form.lead_name,
    lead_partner_name: form.lead_partner_name.trim() || null,
    excluded_investors: lines(form.excluded_investors),
    investor_statuses: form.statuses.filter(row => row.investor_name.trim() || row.investor_id).map(row => ({
      investor_name: row.investor_name || null, investor_id: row.investor_id || null, statuses: [row.status],
    })),
    preferences: { include_angels: form.include_angels, include_corporate_vcs: form.include_corporate_vcs,
      investor_countries: lines(form.investor_countries) },
  };
}

function Field({ title, optional, hint, children, className = "" }: {
  title: string; optional?: boolean; hint?: string; children: ReactNode; className?: string;
}) {
  const id = useId();
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<{ id?: string; "aria-describedby"?: string }>, {
        id, "aria-describedby": hint ? `${id}-hint` : undefined,
      }) : children;
  return <div className={`field ${className}`}><label htmlFor={id} className="field-label">{title}{optional && <span>Optional</span>}</label>
    {control}{hint && <span id={`${id}-hint`} className="field-hint">{hint}</span>}</div>;
}

function Sources({ sources, title = "Cala sources" }: { sources: Source[]; title?: string }) {
  const unique = sources.filter((source, index) => sources.findIndex(other => other.url === source.url) === index);
  if (!unique.length) return null;
  return <details className="sources"><summary><Link2 size={12} />{unique.length} {title.toLowerCase()}<ChevronDown size={12} /></summary>
    <div className="source-list">{unique.map(source => <div className="source-item" key={source.url}>
      <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label || "Cala source"}<ExternalLink size={11} /></a>
      <span>{new URL(source.url).hostname}</span>
      <small>{source.published_at ? `Published ${dateLabel(source.published_at)} · ` : ""}Retrieved {dateLabel(source.retrieved_at)}</small>
    </div>)}</div></details>;
}

function CopyButton({ text, title = "Copy request", className = "button secondary" }: { text: string; title?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true); setFailed(false);
      timer.current = setTimeout(() => setCopied(false), 2200);
    } catch { setFailed(true); }
  }
  return <span className="copy-wrap"><button type="button" className={className} onClick={copy}>
    {copied ? <CheckCheck size={15} /> : <Copy size={15} />}{copied ? "Copied" : title}
  </button>{failed && <small role="status">Select the draft below and copy it manually.</small>}</span>;
}

function NetworkGraphic() {
  return <div className="network-graphic" aria-hidden="true">
    <svg viewBox="0 0 280 180" fill="none"><path d="M140 125V70M140 70L61 39M140 70L219 39M140 70L237 107M140 70L43 107" stroke="currentColor" strokeWidth="1" />
      <circle cx="140" cy="125" r="24" fill="white" stroke="currentColor" /><circle cx="140" cy="70" r="13" fill="currentColor" />
      <circle cx="61" cy="39" r="17" fill="white" stroke="currentColor" /><circle cx="219" cy="39" r="17" fill="white" stroke="currentColor" />
      <circle cx="237" cy="107" r="11" fill="white" stroke="currentColor" /><circle cx="43" cy="107" r="11" fill="white" stroke="currentColor" />
      <path d="M132 125h16m-8-8v16M57 39h8m150 0h8" stroke="currentColor" strokeWidth="1.5" />
      <text x="140" y="169" textAnchor="middle">YOUR ROUND</text>
    </svg><span className="graphic-label">Your lead&apos;s shared investments</span></div>;
}

function Trace({ events, live = false }: { events: ProgressEvent[]; live?: boolean }) {
  const visible = events.slice(-40);
  return <div className="trace"><div className="trace-title"><span><Radio size={14} />Agent activity</span>
    <span className="mono">{live ? "LIVE" : `${events.length} EVENTS`}</span></div>
    <div className="trace-list" aria-live="polite" aria-relevant="additions">{visible.length ? visible.map(event =>
      <details className={`trace-event ${event.type === "complete" ? "trace-complete" : ""}`} key={event.id}>
        <summary><span className="trace-dot" /><span className="trace-content"><span>{event.message}</span>
          <small>{event.tool || label(event.step)}{event.cached === true ? " · cache hit" : ""}{event.count != null ? ` · ${event.count} results` : ""}</small>
        </span><time className="mono">{new Date(event.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></summary>
        {event.query && <pre>{JSON.stringify(event.query, null, 2)}</pre>}
      </details>) : <div className="trace-placeholder">Waiting for the first tool action…</div>}</div></div>;
}

function TrustSection({ card, busy, onConfirmLoop }: { card: CandidateCard; busy: boolean; onConfirmLoop?: (card: CandidateCard, loopId: string, confirmed: boolean) => void }) {
  const loops = card.loops || [];
  return <div className="card-section trust-section"><h4><Network size={14} />How reachable they are</h4>
    {card.trust == null ? <p className="small-muted">Cala returned no people data connecting you to this investor. Nothing is inferred.</p>
      : !loops.length ? <p className="small-muted">No mutual connection within four steps was found in the retrieved roles.</p>
      : <ul className="trust-loops">{loops.map(loop => <li key={loop.id}><div className="trust-loop-heading"><strong>{label(loop.kind)}{loop.closes_through_lead ? " · closes through your lead" : ""}</strong></div>
        <ol>{loop.hops.map((hop, idx) => <li key={idx}>{hopText(hop)} <span>{hopDates(hop, dateLabel)}</span><Sources sources={hop.sources} title="sources" /></li>)}</ol>
        <p className="small-muted">{loop.label}</p>
        {onConfirmLoop && loop.contact && <label className="check-row"><input type="checkbox" disabled={busy} checked={loop.confirmed} onChange={event => onConfirmLoop(card, loop.id, event.target.checked)} /><span>I know {loop.contact.name} (reruns the search)</span></label>}</li>)}</ul>}
    {card.recommended_action && <ActionLadder card={card} />}
  </div>;
}

function Candidate({ card, index, onExclude, onConfirmLoop, busy, initiallyExpanded = false, scoringVersion = "main-1fed306" }: { card: CandidateCard; index: number; onExclude: (card: CandidateCard) => void; onConfirmLoop?: (card: CandidateCard, loopId: string, confirmed: boolean) => void; busy: boolean; initiallyExpanded?: boolean; scoringVersion?: string }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  // Rule scoring: one point per rule met. Older saved results keep their 0-100 display.
  const ruleScoring = scoringVersion.startsWith("rules-");
  const mainScoring = scoringVersion.startsWith("main-") || scoringVersion.startsWith("v2-") || ruleScoring;
  return <article className="candidate-card">
    <div className="candidate-heading"><div className="candidate-identity"><span className="candidate-rank mono">{String(index + 1).padStart(2, "0")}</span>
      <div><h3>{card.investor.name}</h3><div className="meta-row"><EntityIdentity entity={card.investor} /></div></div></div>
      <div className="fit-score">{ruleScoring ? <><strong>{card.fit_score}</strong><small>{card.fit_score === 1 ? "rule met" : "rules met"}</small></>
        : <><strong>{card.fit_score}<span>/100</span></strong><small>Introduction priority</small></>}</div></div>
    <p className="candidate-explanation">{card.explanation}</p>
    {card.flags?.length ? <div className="card-section main-score-flags"><h4>{card.review_status === "flagged" ? "Needs review" : "Scoring considerations"}</h4>
      <ul>{card.flags.map((flag, idx) => <li className={`main-score-flag main-score-flag-${flag.severity}`} key={`${flag.kind}-${idx}`}>
        <strong>{label(flag.kind)}</strong><p>{flag.reason}</p><Sources sources={flag.sources || []} title="sources" /></li>)}</ul></div> : null}
    {card.statuses.length > 0 && <div className="status-chips">{card.statuses.map(status => <span className="chip" key={status}>{STATUS_LABELS[status]}</span>)}</div>}
    {(card.origin_rounds || []).length > 0 && <div className="card-section"><h4><GitBranch size={14} />Your founder network</h4>
      <div className="status-chips">{(card.origins || []).map(origin => <span className="chip" key={origin}>{ORIGIN_LABELS[origin]}</span>)}</div>
      <div className="shared-deals">{card.origin_rounds!.map(deal => <div className="shared-deal" key={deal.id}><span className="small-dot" /><div><strong>{deal.company.name}</strong>
        <span>{deal.stage ? label(deal.stage) : "Stage unknown"} · {dateLabel(deal.event_date, deal.event_date_precision)}</span><Sources sources={deal.sources} title="sources" /></div></div>)}</div></div>}
    <div className="card-section"><h4><GitBranch size={14} />The connection to your lead</h4><div className="shared-deals">
      {!card.shared_deals.length && <p className="small-muted">No shared round with your lead was found in the retrieved data. This investor is here through your founder network.</p>}
      {card.shared_deals.map(deal => <div className="shared-deal" key={deal.id}><span className="small-dot" /><div><strong>{deal.company.name}</strong>
        <span>{deal.stage ? label(deal.stage) : "Stage unknown"} · {dateLabel(deal.event_date, deal.event_date_precision)}</span><Sources sources={deal.sources} title="sources" /></div></div>)}</div></div>
    <TrustSection card={card} busy={busy} onConfirmLoop={onConfirmLoop} />
    {card.considerations.length > 0 && <div className="card-section consideration-section"><h4><CircleHelp size={14} />Questions to take into the conversation</h4>
      <ul className="considerations">{card.considerations.map((item, idx) => <li key={`${item.code}-${idx}`}>
        <span className="consideration-basis">{item.basis}</span><p>{item.text}</p><Sources sources={item.sources} title="sources" /></li>)}</ul></div>}
    <div className="card-actions"><CopyButton text={card.draft} title={card.action === "follow_up" ? "Copy follow-up request" : "Copy introduction request"} className="button primary" />
      <button type="button" className="button text-button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? "Less detail" : "View fit & draft"}<ChevronDown size={14} className={expanded ? "rotated" : ""} /></button></div>
    {expanded && <div className="candidate-details"><EntityMetadata entity={card.investor} /><div className="detail-title"><h4>{ruleScoring ? "Which rules this investor meets" : "How the fit score is built"}</h4><span>{ruleScoring ? "Sourced Cala facts; not an investment probability" : "Heuristic, not investment probability"}</span></div>
      {ruleScoring ? <RuleChecklist card={card} renderSources={sources => <Sources sources={sources} title="sources" />} />
        : <div className="component-list">{card.components.map(item => <div className="component" key={item.name}>
        <div><strong>{label(item.name)}</strong><span className="mono">{item.known ? `${Math.round(item.value * 100)}%` : "Unknown"} <small>· weight {item.weight}</small></span></div>
        <div className="component-track"><span style={{ width: `${item.known ? item.value * 100 : mainScoring ? 0 : 50}%` }} className={item.known ? "" : "neutral"} /></div>
        <p>{item.explanation}{item.known && item.numerator != null && item.denominator != null ? ` (${Number(item.numerator.toFixed(2))}/${item.denominator})` : ""}</p>
        <Sources sources={item.sources} title="sources" /></div>)}</div>}
      {!ruleScoring && <div className="detail-note"><FileCheck2 size={15} /><p>Completeness describes available profile fields. {mainScoring ? "Unknown components contribute zero points; their weights are never redistributed." : "Unknown components retain a neutral value of 0.5; their weights are never redistributed."}</p></div>}
      {card.estimated_ticket_eur && <div className="ticket-estimate"><h4>Equal-share ticket estimate</h4><p><strong>{money(card.estimated_ticket_eur)}</strong> from historical EUR round totals divided by participant counts. This is a planning estimate; actual individual checks are unknown.</p></div>}
      <div className="ticket-detail"><h4>Participation size</h4>{card.profile.ticket_criteria.length ? card.profile.ticket_criteria.map((ticket, idx) => <div key={idx}>
        <p><strong>{label(ticket.kind)}</strong>: {money(ticket.minimum_eur)} – {money(ticket.maximum_eur)}. {ticket.currently_applicable ? "Currently applicable." : "Current applicability to confirm."}</p><Sources sources={ticket.sources} /></div>) : <p>Unknown. A company&apos;s total funding round is not an individual investor&apos;s check.</p>}
        <p className="small-muted">Most recent observed investment: {dateLabel(card.most_recent_investment)}</p></div>
      <div className="draft-box"><div><h4>{card.action === "follow_up" ? "Your follow-up request" : "Your introduction request"}</h4><CopyButton text={card.draft} title="Copy" className="button icon-button" /></div><pre tabIndex={0}>{card.draft}</pre></div>
      <Sources sources={card.sources} /><div className="exclude-action"><span>You control who is approached.</span><button type="button" className="button text-button" disabled={busy} onClick={() => onExclude(card)}><X size={13} />Do not contact & rerun</button></div>
    </div>}
  </article>;
}

export default function Home() {
  const [form, setForm] = useState<FormData>(INITIAL);
  const [phase, setPhase] = useState<Phase>("identify");
  const [matches, setMatches] = useState<CompanyMatches | null>(null);
  const [identity, setIdentity] = useState<OnboardingConfirmation | null>(null);
  const [selectedLead, setSelectedLead] = useState<string | null>(null);
  const [selectedStartup, setSelectedStartup] = useState<string | null>(null);
  const [profileReviewed, setProfileReviewed] = useState(false);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [streamNotice, setStreamNotice] = useState(false);
  const [recoveryAvailable, setRecoveryAvailable] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const lookupController = useRef<AbortController | null>(null);
  const lookupVersion = useRef(0);
  useEffect(() => () => { lookupVersion.current += 1; lookupController.current?.abort(); }, []);

  useEffect(() => { getHealth().then(health => setConnected(health.cala_configured)).catch(() => setConnected(false)); }, []);
  useEffect(() => { if (phase !== "running" || !run) return;
    const update = () => {
      const seconds = Math.max(0, Math.floor((Date.now() - new Date(run.confirmed_at).getTime()) / 1000));
      setElapsed(seconds);
      if (seconds > 100) setRecoveryAvailable(true);
    };
    update(); const timer = setInterval(update, 1000); return () => clearInterval(timer);
  }, [phase, run]);
  useEffect(() => {
    if (!run) return;
    let disposed = false, polling = false;
    const unsubscribe = subscribeRunEvents(run.id, event => {
      if (!disposed) setEvents(previous => previous.some(item => item.id === event.id) ? previous : [...previous, event].slice(-400));
    }, () => { if (!disposed) setStreamNotice(true); });
    const poll = async () => {
      if (disposed || polling) return;
      polling = true;
      try {
        const final = await getRunResult(run.id);
        if (final && !disposed) { setResult(final); setPhase("results"); setBusy(false); setError(null); clearInterval(timer); unsubscribe(); }
      } catch (err) { if (!disposed) { setError(errorMessage(err)); setRecoveryAvailable(true); } }
      finally { polling = false; }
    };
    const timer = setInterval(poll, 1000); void poll();
    return () => { disposed = true; clearInterval(timer); unsubscribe(); };
  }, [run?.id]);

  function update<K extends keyof FormData>(key: K, value: FormData[K]) {
    setForm(previous => ({ ...previous, [key]: value }));
    setProfileReviewed(false);
  }
  function scrollTop() { requestAnimationFrame(() => mainRef.current?.scrollIntoView({ block: "start", behavior: "instant" })); }
  function changeCompanies() {
    lookupVersion.current += 1; lookupController.current?.abort(); lookupController.current = null;
    setForm(previous => ({ ...INITIAL, company_name: previous.company_name, lead_name: previous.lead_name }));
    setMatches(null); setIdentity(null); setSelectedLead(null); setSelectedStartup(null);
    setProfileReviewed(false); setRun(null); setResult(null); setEvents([]);
    setError(null); setBusy(false); setPhase("identify"); scrollTop();
  }
  async function findCompanies(event: FormEvent) {
    event.preventDefault();
    if (busy || !form.company_name.trim() || !form.lead_name.trim()) return;
    lookupController.current?.abort();
    const controller = new AbortController(); lookupController.current = controller;
    const version = ++lookupVersion.current;
    setBusy(true); setError(null); setIdentity(null); setProfileReviewed(false);
    try {
      const found = await findCompanyMatches(form.company_name.trim(), form.lead_name.trim(), controller.signal);
      if (controller.signal.aborted || version !== lookupVersion.current) return;
      setMatches(found); setSelectedLead(null); setSelectedStartup(null);
      setPhase("matches"); scrollTop();
    } catch (err) {
      if (!controller.signal.aborted && version === lookupVersion.current) setError(errorMessage(err));
    } finally {
      if (version === lookupVersion.current) { setBusy(false); lookupController.current = null; }
    }
  }
  async function confirmCompanies() {
    if (!matches || !selectedLead || !selectedStartup || busy) return;
    const controller = new AbortController(); lookupController.current = controller;
    const version = ++lookupVersion.current;
    setBusy(true); setError(null);
    try {
      const accepted = await confirmCompanyMatches(matches.id, selectedStartup, selectedLead, controller.signal);
      if (controller.signal.aborted || version !== lookupVersion.current) return;
      setIdentity(accepted);
      setForm({ ...INITIAL, company_name: accepted.startup.name, lead_name: accepted.lead.name,
        website: accepted.prefill.website || "", sector: accepted.prefill.sector || "", country: accepted.prefill.country || "" });
      setProfileReviewed(false); setPhase("details"); scrollTop();
    } catch (err) {
      if (!controller.signal.aborted && version === lookupVersion.current) {
        if (err instanceof ApiError && err.status === 404) { changeCompanies(); setError("Your company matches expired. Find matching companies again."); }
        else setError(errorMessage(err));
      }
    } finally {
      if (version === lookupVersion.current) { setBusy(false); lookupController.current = null; }
    }
  }
  async function beginRun(profile?: FounderInput) {
    if (!identity || busy) return;
    setBusy(true); setError(null);
    try {
      const original = profile || makeProfile(form);
      const snapshot: FounderInput = { ...original, lead_name: identity.lead.name,
        startup: { ...original.startup, company_name: identity.startup.name, cala_entity_id: identity.startup.id } };
      const started = await startRun(identity.resolution_id, identity.lead.id, snapshot);
      setEvents([]); setResult(null); setStreamNotice(false); setRecoveryAvailable(false); setRun(started); setPhase("running"); scrollTop();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) { changeCompanies(); setError("Your confirmed companies expired. Match and confirm them again before searching."); }
      else setError(errorMessage(err));
    } finally { setBusy(false); }
  }
  function completeRound(event: FormEvent) {
    event.preventDefault();
    if (profileReviewed) void beginRun();
  }
  async function exclude(card: CandidateCard) {
    if (!result) return;
    const snapshot: FounderInput = { ...result.profile, startup: { ...result.profile.startup },
      investor_statuses: [...result.profile.investor_statuses, { investor_id: card.investor.id, investor_name: card.investor.name, statuses: ["do_not_contact"] }] };
    setForm(previous => ({ ...previous, statuses: [...previous.statuses, { investor_id: card.investor.id, investor_name: card.investor.name, status: "do_not_contact" }] }));
    await beginRun(snapshot);
  }
  async function confirmLoop(card: CandidateCard, loopId: string, confirmed: boolean) {
    if (!result) return;
    const current = result.profile.confirmed_loop_ids || [];
    const ids = confirmed ? [...new Set([...current, loopId])] : current.filter(id => id !== loopId);
    await beginRun({ ...result.profile, startup: { ...result.profile.startup }, confirmed_loop_ids: ids });
  }
  function editProfile() { setPhase(identity ? "details" : matches ? "matches" : "identify"); setError(null); scrollTop(); }
  let previewGap: number | null = null;
  try { previewGap = Math.max(0, Number(euroAmount(form.target_eur, "Round target", true)) - Number(euroAmount(form.firm_commitments_eur, "Already committed"))); } catch { /* Show the preview after both amounts are valid. */ }
  const activeStep = activeRunStage(events);


  return <div className="app-shell"><header className="topbar"><a className="brand" href="/" aria-label="RoundCloser home"><span className="brand-mark"><GitBranch size={20} strokeWidth={1.7} /></span>roundcloser<span className="brand-beta">BETA</span></a>
    <nav className="pill-nav" aria-label="Workflow"><button className={phase !== "results" ? "active" : ""} onClick={editProfile} disabled={phase === "running"}><Building2 size={14} /><span>Your round</span></button>
      <button className={phase === "results" ? "active" : ""} onClick={() => result && setPhase("results")} disabled={!result || phase === "running"}><Layers3 size={14} /><span>Your shortlist</span></button></nav>
    <div className="connection-badge"><span className={connected ? "connection-dot connected" : "connection-dot"} />{connected == null ? "Connecting to Cala" : connected ? "Powered by Cala" : "Cala not connected"}</div></header>
    <main className={`main-panel ${phase === "results" ? "main-panel-results" : ""}`} ref={mainRef}><div className={`content-width ${phase === "results" ? "results-content" : phase === "running" ? "progress-content" : ""}`}>
      <div className="page-topline"><span className="eyebrow"><span className="small-dot" />A clearer path to your next introduction</span><span className="page-count mono">{phase === "results" ? "02 / 02" : "01 / 02"}</span></div>
      {phase !== "running" && phase !== "results" && <ol className="onboarding-progress" aria-label="Round setup progress">{["Find companies", "Confirm matches", "Complete your round"].map((step, index) => {
        const current = phase === "identify" ? 0 : phase === "matches" ? 1 : 2;
        return <li key={step} className={index === current ? "is-current" : index < current ? "is-complete" : ""} aria-current={index === current ? "step" : undefined}><span>{index < current ? <Check size={12} /> : index + 1}</span>{step}</li>;
      })}</ol>}

      {phase === "identify" && <><div className="hero"><div><h1>Start with your company.<br /><span>Find the right lead.</span></h1><p>Tell us the two company names. Confirm the Cala matches,<br className="desktop-break" /> then complete the details that public data cannot tell us.</p></div><div className="hero-note"><ShieldCheck size={17} /><span>Cala facts.<br /><strong>Your decision.</strong></span></div></div>
        <div className="workspace-grid"><form className="profile-form identity-search-form" onSubmit={findCompanies}>
          <section className="form-section"><div className="section-heading"><span className="section-number mono">01</span><div><h2>Find your companies</h2><p>We will show potential matches for you to choose from.</p></div></div>
            <div className="form-grid"><Field title="Company name" className="span-two"><input required disabled={busy} maxLength={200} value={form.company_name} onChange={event => update("company_name", event.target.value)} placeholder="Your company" autoComplete="organization" /></Field>
              <Field title="Lead investor company name" className="span-two"><input required disabled={busy} maxLength={200} value={form.lead_name} onChange={event => update("lead_name", event.target.value)} placeholder="Your lead investor's company" /></Field></div>
          </section>
          {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>{error}</span></div>}
          <div className="form-footer"><span><ShieldCheck size={14} />You confirm both company identities.</span><button className="button primary large" disabled={busy || !form.company_name.trim() || !form.lead_name.trim()} type="submit">{busy ? <LoaderCircle size={16} className="spinning" /> : <Network size={16} />}{busy ? "Finding companies..." : "Find matching companies"}<ArrowRight size={16} /></button></div>
        </form><aside className="guidance-sidebar"><div className="guidance-card"><span className="eyebrow">A connection, with context</span><NetworkGraphic /><h3>The right companies.<br />A clearer starting point.</h3><p>Confirm your company and lead first. We then prefill known details and leave missing information for you to complete.</p><div className="guidance-divider" /><ul className="benefit-list"><li><Building2 size={16} /><span>Choose the correct Cala entities</span></li><li><FileCheck2 size={16} /><span>Review supported company details</span></li><li><Copy size={16} /><span>Find a useful next introduction</span></li></ul></div></aside></div></>}

      {phase === "matches" && matches && <><button className="back-link" onClick={changeCompanies} disabled={busy}><ArrowLeft size={14} />Edit company names</button><div className="hero compact"><div><h1>Let's match the names.<br /><span>You choose the companies.</span></h1><p>Select your company and lead investor. We will use these identities to prefill your round form.</p></div></div>
        <div className="workspace-grid"><div className="confirmation-main">
          <section className="confirmation-section startup-matches"><div className="section-heading"><span className="section-number mono">01</span><div><h2>Choose your company</h2><p>Cala matches for &ldquo;{form.company_name}&rdquo;</p></div></div>
            <div className="entity-options">{matches.startup_matches.map(entity => <EntityOption key={entity.id} entity={entity} selected={selectedStartup === entity.id} disabled={busy} onSelect={() => { setSelectedStartup(entity.id); }} />)}</div>
            {!matches.startup_matches.length && <div className="empty-inline"><Building2 size={24} /><h3>No company match found</h3><p>Try your company's full name or another spelling.</p><button type="button" className="button secondary" onClick={changeCompanies}><ArrowLeft size={14} />Refine company names</button></div>}
          </section>
          <section className="confirmation-section lead-matches"><div className="section-heading"><span className="section-number mono">02</span><div><h2>Choose your lead</h2><p>Cala matches for &ldquo;{form.lead_name}&rdquo;</p></div></div>
            <div className="entity-options">{matches.lead_matches.map(entity => <EntityOption key={entity.id} entity={entity} selected={selectedLead === entity.id} disabled={busy} onSelect={() => { setSelectedLead(entity.id); }} />)}</div>
            {!matches.lead_matches.length && <div className="empty-inline"><Network size={24} /><h3>No lead match found</h3><p>Try the investor's full organization name or another spelling.</p><button type="button" className="button secondary" onClick={changeCompanies}><ArrowLeft size={14} />Refine company names</button></div>}
          </section>
          <small className="resolver-time mono">COMPANY LOOKUP &middot; {(matches.resolver_ms / 1000).toFixed(1)}s</small>
          {matches.notices.map((notice, index) => <div className="inline-note" key={index}><CircleHelp size={15} /><p>{notice}</p></div>)}
          {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>{error}</span></div>}
          <div className="form-footer"><span><FileCheck2 size={14} />Next, review the prefilled details.</span><button type="button" className="button primary large" disabled={busy || !selectedStartup || !selectedLead} onClick={() => void confirmCompanies()}>{busy ? <LoaderCircle size={16} className="spinning" /> : <Check size={16} />}{busy ? "Confirming companies..." : "Confirm companies"}<ArrowRight size={16} /></button></div>
        </div><aside className="guidance-sidebar"><div className="guidance-card"><span className="eyebrow">Before the round details</span><NetworkGraphic /><h3>Check the identity.<br />Then add your context.</h3><p>Use the website, headquarters, description and source links to distinguish companies with similar names.</p><div className="guidance-divider" /><p>Confirming these companies does not start an investor search or assume a lead commitment.</p></div></aside></div></>}

      {phase === "details" && identity && <><button className="back-link" onClick={changeCompanies} disabled={busy}><ArrowLeft size={14} />Change companies</button><div className="hero compact"><div><h1>Your companies, confirmed.<br /><span>Complete your round.</span></h1><p>Known company details are prefilled. Review them and fill the gaps before we search.</p></div></div>
        <div className="confirmed-companies" aria-label="Confirmed companies"><div><span>Your company</span><strong>{identity.startup.name}</strong></div><div><span>Your lead investor</span><strong>{identity.lead.name}</strong></div><span className="confirmed-companies-badge"><Check size={13} />Identities confirmed</span></div>
        <div className="workspace-grid"><form className="profile-form round-details-form" onSubmit={completeRound}>
          <section className="form-section"><div className="section-heading"><span className="section-number mono">01</span><div><h2>Your company details</h2><p>Review Cala's available details and add anything missing.</p></div></div>
            <div className="form-grid"><Field title="Company name"><input readOnly value={form.company_name} /></Field>
              <Field title="Founder name" optional><input maxLength={200} value={form.founder_name} onChange={event => update("founder_name", event.target.value)} autoComplete="name" /></Field>
              <Field title="Company website" optional hint="A domain such as cala.ai is enough."><input value={form.website} onChange={event => update("website", event.target.value)} placeholder="Company website, if known" autoComplete="url" /></Field>
              <Field title="Sector" hint="Review the prefill or enter your current sector."><input required maxLength={200} value={form.sector} onChange={event => update("sector", event.target.value)} placeholder="Your sector" /></Field>
              <Field title="Headquarters country" optional><input maxLength={100} value={form.country} onChange={event => update("country", event.target.value)} placeholder="Country, if known" autoComplete="country-name" /></Field>
              <Field title="Confirmed lead investor"><input readOnly value={form.lead_name} /></Field>
              <Field title="Lead partner name" optional hint="The partner at your lead who owns this deal. Used to find trust loops through your lead."><input maxLength={200} value={form.lead_partner_name} onChange={event => update("lead_partner_name", event.target.value)} placeholder="Partner name, if known" /></Field></div>
            {Object.values(identity.prefill_sources).some(sources => sources.length > 0) && <details className="prefill-evidence"><summary><Link2 size={13} />Company prefill sources<ChevronDown size={13} /></summary><div>{Object.entries(identity.prefill_sources).filter(([, sources]) => sources.length > 0).map(([field, sources]) => <div key={field}><strong>{field === "country" ? "Headquarters country" : field === "company_name" ? "Company name" : label(field)}</strong><Sources sources={sources} title="prefill sources" /></div>)}</div></details>}
          </section>
          <section className="form-section"><div className="section-heading"><span className="section-number mono">02</span><div><h2>Your current round</h2><p>Historical funding does not establish this round's stage, target or commitments.</p></div></div>
            <div className="form-grid"><Field title="Round stage"><select required value={form.stage} onChange={event => update("stage", event.target.value as FormData["stage"])}><option value="">Choose your round stage</option><option value="pre_seed">Pre-seed</option><option value="seed">Seed</option><option value="series_a">Series A</option><option value="series_b">Series B</option></select></Field>
              <Field title="Lead commitment status"><select value={form.lead_status} onChange={event => update("lead_status", event.target.value as FormData["lead_status"])}><option value="unconfirmed">Current-round commitment unconfirmed</option><option value="signed">Signed</option><option value="firm_verbal">Firm verbal commitment</option><option value="conditional">Conditional interest</option></select></Field>
              <Field title="Round target (EUR)" hint="Numbers, K or M; for example 4M."><input required inputMode="decimal" value={form.target_eur} onChange={event => update("target_eur", event.target.value)} placeholder="Your round target" /></Field>
              <Field title="Already committed (EUR)" hint="Firm commitments only. Enter 0 if none."><input required inputMode="decimal" value={form.firm_commitments_eur} onChange={event => update("firm_commitments_eur", event.target.value)} placeholder="Firm commitments" /></Field>
              <Field title="Investors already in (excluded)" className="span-two" optional hint="Separate names with commas. These investors are excluded from suggestions."><input value={form.excluded_investors} onChange={event => update("excluded_investors", event.target.value)} placeholder="Investor names" /></Field>
              <Field title="Known competitors" className="span-two" optional hint="Separate company names with commas."><input value={form.known_competitors} onChange={event => update("known_competitors", event.target.value)} placeholder="Company names" /></Field></div>
          </section>
          <details className="advanced-controls"><summary>Additional relationship controls<ChevronDown size={14} /></summary><div className="advanced-controls-content">
            {form.lead_status === "conditional" && <div className="inline-note"><CircleHelp size={15} /><p>Results and introduction requests will describe this lead commitment as provisional.</p></div>}
            <div className="status-rows">{form.statuses.map((row, index) => <div className="status-row" key={index}>
              <input aria-label={`Investor ${index + 1} name`} required value={row.investor_name} placeholder="Investor name" onChange={event => update("statuses", form.statuses.map((item, idx) => idx === index ? { ...item, investor_name: event.target.value, investor_id: undefined } : item))} />
              <select aria-label={`Investor ${index + 1} status`} value={row.status} onChange={event => update("statuses", form.statuses.map((item, idx) => idx === index ? { ...item, status: event.target.value as InvestorStatus } : item))}>{Object.entries(STATUS_LABELS).map(([value, name]) => <option value={value} key={value}>{name}</option>)}</select>
              <button type="button" className="remove-button" aria-label={`Remove investor ${index + 1}`} onClick={() => update("statuses", form.statuses.filter((_, idx) => idx !== index))}><X size={15} /></button></div>)}</div>
            <button type="button" className="button text-button add-status" onClick={() => update("statuses", [...form.statuses, { investor_name: "", status: "existing_shareholder" }])}><Plus size={14} />Add an investor status</button>
            <div className="preference-options"><label className="check-row"><input type="checkbox" checked={form.include_angels} onChange={event => update("include_angels", event.target.checked)} /><span>Include angels</span></label><label className="check-row"><input type="checkbox" checked={form.include_corporate_vcs} onChange={event => update("include_corporate_vcs", event.target.checked)} /><span>Include corporate VCs</span></label></div>
            <Field title="Investor countries" optional hint="Leave blank for all countries. Separate countries with commas."><input value={form.investor_countries} onChange={event => update("investor_countries", event.target.value)} placeholder="Finland, Sweden, United Kingdom" /></Field>
          </div></details>
          <label className="check-row confirm-check"><input type="checkbox" checked={profileReviewed} onChange={event => setProfileReviewed(event.target.checked)} /><span>I have reviewed my round details.</span></label>
          {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>{error}</span></div>}
          <div className="form-footer"><span><Clock3 size={14} />The search starts after your review.</span><button className="button primary large" disabled={busy || !profileReviewed} type="submit">{busy ? <LoaderCircle size={16} className="spinning" /> : <Network size={16} />}{busy ? "Starting your search..." : "Find my co-investors"}<ArrowRight size={16} /></button></div>
        </form><aside className="guidance-sidebar"><div className="guidance-card"><span className="eyebrow">Your details, reviewed</span><NetworkGraphic /><h3>Public facts.<br />Your current round.</h3><p>Cala supplies the known company details. You supply the current fundraising plan and confirm what we should use.</p><div className="guidance-divider" /><p>Fields without supported data stay empty. You can edit every prefilled detail before starting.</p></div><div className="allocation-preview"><span className="eyebrow">Your allocation preview</span><strong>{previewGap == null ? "—" : money(previewGap)}</strong><span>Remaining after firm commitments</span></div><p className="sidebar-footnote"><CircleHelp size={14} />You choose what to ask, and who to approach.</p></aside></div>
        {identity.notices.map((notice, index) => <div className="inline-note" key={index}><CircleHelp size={15} /><p>{notice}</p></div>)}
      </>}

      {phase === "running" && <><div className="hero compact"><div><span className="running-label"><span className="live-dot" />Search in progress</span><h1>Following the connections.<br /><span>Building your shortlist.</span></h1><p>Exploring your lead and founder networks, reviewing investor evidence, and preparing your shortlist and requests.</p></div><div className="timer"><strong className="mono">{String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(elapsed % 60).padStart(2, "0")}</strong><span>of a 120-second target</span></div></div>
        <div className="progress-steps" role="list" aria-label="Search progress">{RUN_STAGES.map((step, idx) => <div role="listitem" aria-current={idx === activeStep ? "step" : undefined} key={step} className={idx === activeStep ? "current" : idx < activeStep ? "done" : ""}><span>{idx < activeStep ? <Check size={13} /> : idx === activeStep ? <LoaderCircle size={13} className="spinning" /> : String(idx + 1).padStart(2, "0")}</span><strong>{step}</strong></div>)}</div>
        <div className="workspace-grid progress-grid"><div><Trace events={events} live />{streamNotice && <div className="inline-note"><Radio size={15} /><p>The live trace is reconnecting. Result polling is still active.</p></div>}{error && <div className="error-banner" role="alert"><CircleHelp size={15} /><span>{error}</span></div>}{recoveryAvailable && <div className="recovery-actions" role="status"><p>The result is taking longer than expected, or this run is no longer available. You can return to your profile and start a fresh search.</p><button type="button" className="button secondary" onClick={() => { setRun(null); editProfile(); }}><ArrowLeft size={14} />Return to your profile</button></div>}</div><aside className="guidance-card progress-aside"><picture className="progress-network-picture"><source media="(prefers-reduced-motion: reduce)" srcSet="/obsidian-graph.jpg" /><Image unoptimized className="progress-network-image" src="/obsidian-network.gif" alt="Illustration of an interconnected network of nodes" width={600} height={600} /></picture><div className="progress-aside-copy"><h3>A useful shortlist<br />can be a short one.</h3><p>We return up to five investors with sourced connections: a shared round with your lead, your own cap table, or a founder's previous company. When evidence is sparse, you&apos;ll see fewer results and clear gaps.</p><div className="guidance-divider" /><p className="small-muted">The timer started with your confirmation. Unfinished profiles remain incomplete; they are never silently rejected.</p></div></aside></div></>}

      {phase === "results" && result && <ResultsWorkspace key={result.run_id} result={result} busy={busy} error={error}
        onEdit={editProfile} onRerun={() => void beginRun(result.profile)}
        renderInvestorDetails={(card, index) => <Candidate card={card} index={index} initiallyExpanded scoringVersion={result.scoring_version} busy={busy} onExclude={candidate => void exclude(candidate)} onConfirmLoop={(candidate, loopId, confirmed) => void confirmLoop(candidate, loopId, confirmed)} />}
        renderSources={sources => <Sources sources={sources} title="sources" />} trace={<Trace events={events} />} />}
      <footer className="page-footer"><span>roundcloser</span><span className="footer-support">Supported by Cala AI</span></footer>
    </div></main></div>;
}

function EntityOption({ entity, selected, onSelect, disabled = false }: { entity: Entity; selected: boolean; onSelect: () => void; disabled?: boolean }) {
  return <div className={`entity-option ${selected ? "selected" : ""}`}><button type="button" className="entity-choice" onClick={onSelect} disabled={disabled} aria-pressed={selected}>
    <span className="selection-circle">{selected && <Check size={13} />}</span><div><strong>{entity.name}</strong><EntityIdentity entity={entity} />{entity.description && <p>{entity.description}</p>}</div></button>
    <div className="entity-evidence">{entity.website && <a href={entity.website} target="_blank" rel="noopener noreferrer">{new URL(entity.website).hostname}<ExternalLink size={11} /></a>}<Sources sources={entity.sources} title="sources" /><small className="mono">{entity.id}</small></div><EntityMetadata entity={entity} /></div>;
}
