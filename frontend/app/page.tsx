"use client";

import { cloneElement, isValidElement, useEffect, useId, useRef, useState, type FormEvent, type ReactElement, type ReactNode } from "react";
import {
  ArrowDown, ArrowLeft, ArrowRight, Building2, Check, CheckCheck, ChevronDown,
  CircleHelp, Clock3, Copy, ExternalLink, FileCheck2, GitBranch, Layers3,
  Link2, LoaderCircle, Network, Plus, Radio, RotateCcw, ShieldCheck, Sparkles, X,
} from "lucide-react";
import { ApiError, getHealth, getRunResult, resolveProfile, startRun, subscribeRunEvents } from "../lib/api";
import type { CandidateCard, Entity, FounderInput, InvestorStatus, ProgressEvent, Resolution, RunResult, RunStatus, Source } from "../lib/types";
import ResultsWorkspace from "../components/results-workspace";
import EntityMetadata, { EntityIdentity } from "../components/entity-metadata";

type Phase = "profile" | "confirm" | "running" | "results";
type StatusRow = { investor_name: string; investor_id?: string; status: InvestorStatus };
type FormData = {
  founder_name: string; company_name: string; website: string; sector: string; country: string;
  known_competitors: string; stage: "pre_seed" | "seed" | "series_a" | "series_b"; target_eur: string;
  firm_commitments_eur: string; lead_name: string; excluded_investors: string;
  lead_status: "signed" | "firm_verbal" | "conditional" | "unconfirmed";
  include_angels: boolean; include_corporate_vcs: boolean; investor_countries: string;
  statuses: StatusRow[];
};
const INITIAL: FormData = {
  founder_name: "", company_name: "Cala AI", website: "cala.ai",
  sector: "AI infrastructure, data infrastructure, AI agents", country: "", known_competitors: "", stage: "seed",
  target_eur: "4000000", firm_commitments_eur: "2000000", lead_name: "", excluded_investors: "",
  lead_status: "unconfirmed", include_angels: true,
  include_corporate_vcs: true, investor_countries: "", statuses: [],
};
const STATUS_LABELS: Record<InvestorStatus, string> = {
  existing_shareholder: "Existing shareholder", firm_committed: "Firm committed this round",
  conditional_interest: "Conditional interest", already_approached: "Already approached",
  in_conversation: "In conversation", declined: "Declined this round", do_not_contact: "Do not contact",
};
const STEPS = ["Lead network", "Candidate profiles", "Fit & considerations", "Introduction plan"];
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
  return {
    founder_name: form.founder_name || null,
    startup: { company_name: form.company_name, website: companyWebsite(form.website),
      product_description: "", sector: form.sector, target_customer: "", problem_solved: "",
      country: form.country, known_competitors: lines(form.known_competitors) },
    round: { stage: form.stage, target_eur: euroAmount(form.target_eur, "Round target", true),
      firm_commitments_eur: euroAmount(form.firm_commitments_eur, "Already committed"),
      conditional_interest_eur: "0", lead_status: form.lead_status, lead_ticket_eur: null },
    lead_name: form.lead_name,
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

function Candidate({ card, index, onExclude, busy, initiallyExpanded = false, scoringVersion = "main-1fed306" }: { card: CandidateCard; index: number; onExclude: (card: CandidateCard) => void; busy: boolean; initiallyExpanded?: boolean; scoringVersion?: string }) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const mainScoring = scoringVersion.startsWith("main-");
  return <article className="candidate-card">
    <div className="candidate-heading"><div className="candidate-identity"><span className="candidate-rank mono">{String(index + 1).padStart(2, "0")}</span>
      <div><h3>{card.investor.name}</h3><div className="meta-row"><EntityIdentity entity={card.investor} /></div></div></div>
      <div className="fit-score"><strong>{card.fit_score}<span>/100</span></strong><small>Introduction priority</small></div></div>
    <p className="candidate-explanation">{card.explanation}</p>
    {card.flags?.length ? <div className="card-section main-score-flags"><h4>{card.review_status === "flagged" ? "Needs review" : "Scoring considerations"}</h4>
      <ul>{card.flags.map((flag, idx) => <li className={`main-score-flag main-score-flag-${flag.severity}`} key={`${flag.kind}-${idx}`}>
        <strong>{label(flag.kind)}</strong><p>{flag.reason}</p><Sources sources={flag.sources || []} title="sources" /></li>)}</ul></div> : null}
    {card.statuses.length > 0 && <div className="status-chips">{card.statuses.map(status => <span className="chip" key={status}>{STATUS_LABELS[status]}</span>)}</div>}
    <div className="candidate-metrics"><div><span>Shared rounds</span><strong>{card.shared_round_count}</strong></div>
      <div><span>Shared companies</span><strong>{card.shared_company_count}</strong></div>
      <div><span>Rounds · last 12m</span><strong>{card.recent_rounds_12m ?? <span className="unknown">Unknown</span>}</strong></div>
      <div><span>Profile completeness</span><strong>{Math.round(card.profile_completeness * 100)}<small>%</small></strong></div></div>
    <div className="card-section"><h4><GitBranch size={14} />The connection to your lead</h4><div className="shared-deals">
      {card.shared_deals.map(deal => <div className="shared-deal" key={deal.id}><span className="small-dot" /><div><strong>{deal.company.name}</strong>
        <span>{deal.stage ? label(deal.stage) : "Stage unknown"} · {dateLabel(deal.event_date, deal.event_date_precision)}</span><Sources sources={deal.sources} title="sources" /></div></div>)}</div></div>
    {card.considerations.length > 0 && <div className="card-section consideration-section"><h4><CircleHelp size={14} />Questions to take into the conversation</h4>
      <ul className="considerations">{card.considerations.map((item, idx) => <li key={`${item.code}-${idx}`}>
        <span className="consideration-basis">{item.basis}</span><p>{item.text}</p><Sources sources={item.sources} title="sources" /></li>)}</ul></div>}
    <div className="card-actions"><CopyButton text={card.draft} title={card.action === "follow_up" ? "Copy follow-up request" : "Copy introduction request"} className="button primary" />
      <button type="button" className="button text-button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? "Less detail" : "View fit & draft"}<ChevronDown size={14} className={expanded ? "rotated" : ""} /></button></div>
    {expanded && <div className="candidate-details"><EntityMetadata entity={card.investor} /><div className="detail-title"><h4>How the fit score is built</h4><span>Heuristic, not investment probability</span></div>
      <div className="component-list">{card.components.map(item => <div className="component" key={item.name}>
        <div><strong>{label(item.name)}</strong><span className="mono">{item.known ? `${Math.round(item.value * 100)}%` : "Unknown"} <small>· weight {item.weight}</small></span></div>
        <div className="component-track"><span style={{ width: `${item.known ? item.value * 100 : mainScoring ? 0 : 50}%` }} className={item.known ? "" : "neutral"} /></div>
        <p>{item.explanation}{item.known && item.numerator != null && item.denominator != null ? ` (${Number(item.numerator.toFixed(2))}/${item.denominator})` : ""}</p>
        <Sources sources={item.sources} title="sources" /></div>)}</div>
      <div className="detail-note"><FileCheck2 size={15} /><p>Completeness describes available profile fields. {mainScoring ? "Unknown components contribute zero points; their weights are never redistributed." : "Unknown components retain a neutral value of 0.5; their weights are never redistributed."}</p></div>
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
  const [phase, setPhase] = useState<Phase>("profile");
  const [lookupStartup, setLookupStartup] = useState(true);
  const [resolution, setResolution] = useState<Resolution | null>(null);
  const [selectedLead, setSelectedLead] = useState<string | null>(null);
  const [selectedStartup, setSelectedStartup] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
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

  function update<K extends keyof FormData>(key: K, value: FormData[K]) { setForm(previous => ({ ...previous, [key]: value })); }
  function scrollTop() { requestAnimationFrame(() => mainRef.current?.scrollIntoView({ block: "start", behavior: "instant" })); }
  async function resolve(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const resolved = await resolveProfile(makeProfile(form), lookupStartup);
      setResolution(resolved); setSelectedLead(null); setSelectedStartup(null); setConfirmed(false);
      setPhase("confirm"); scrollTop();
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  async function beginRun(profile?: FounderInput) {
    if (!resolution) return;
    setBusy(true); setError(null);
    const original = profile || resolution.profile;
    const snapshot: FounderInput = { ...original, startup: { ...original.startup, cala_entity_id: selectedStartup } };
    try {
      const started = await startRun(resolution.id, selectedLead, snapshot);
      setEvents([]); setResult(null); setStreamNotice(false); setRecoveryAvailable(false); setRun(started); setPhase("running"); scrollTop();
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  async function exclude(card: CandidateCard) {
    if (!result) return;
    const snapshot: FounderInput = { ...result.profile, startup: { ...result.profile.startup },
      investor_statuses: [...result.profile.investor_statuses, { investor_id: card.investor.id, investor_name: card.investor.name, statuses: ["do_not_contact"] }] };
    setForm(previous => ({ ...previous, statuses: [...previous.statuses, { investor_id: card.investor.id, investor_name: card.investor.name, status: "do_not_contact" }] }));
    await beginRun(snapshot);
  }
  function editProfile() { setPhase("profile"); setError(null); scrollTop(); }
  let previewGap: number | null = null;
  try { previewGap = Math.max(0, Number(euroAmount(form.target_eur, "Round target", true)) - Number(euroAmount(form.firm_commitments_eur, "Already committed"))); } catch { /* Show the preview after both amounts are valid. */ }
  const latestStep = events.at(-1)?.step;
  const activeStep = latestStep === "profiles" ? 1 : latestStep === "assessments" || latestStep === "scoring" ? 2 : latestStep === "complete" ? 3 : 0;
  const detectedCompanyMismatch = Boolean(selectedStartup && resolution?.detected_lead?.company_id
    && selectedStartup !== resolution.detected_lead.company_id);

  return <div className="app-shell"><header className="topbar"><a className="brand" href="/" aria-label="RoundCloser home"><span className="brand-mark"><GitBranch size={20} strokeWidth={1.7} /></span>roundcloser<span className="brand-beta">BETA</span></a>
    <nav className="pill-nav" aria-label="Workflow"><button className={phase === "profile" ? "active" : ""} onClick={editProfile} disabled={phase === "running"}><Building2 size={14} /><span>Your round</span></button>
      <button className={phase === "confirm" || phase === "running" ? "active" : ""} onClick={() => resolution && phase !== "running" && setPhase("confirm")} disabled={!resolution || phase === "running"}><Network size={14} /><span>Connections</span></button>
      <button className={phase === "results" ? "active" : ""} onClick={() => result && setPhase("results")} disabled={!result || phase === "running"}><Layers3 size={14} /><span>Your shortlist</span></button></nav>
    <div className="connection-badge"><span className={connected ? "connection-dot connected" : "connection-dot"} />{connected == null ? "Connecting to Cala" : connected ? "Powered by Cala" : "Cala not connected"}</div></header>
    <main className={`main-panel ${phase === "results" ? "main-panel-results" : ""}`} ref={mainRef}><div className={`content-width ${phase === "results" ? "results-content" : ""}`}>
      <div className="page-topline"><span className="eyebrow"><span className="small-dot" />A clearer path to your next introduction</span><span className="page-count mono">{phase === "profile" ? "01 / 03" : phase === "results" ? "03 / 03" : "02 / 03"}</span></div>
      {phase === "profile" && <><div className="hero"><div><h1>Build your round.<br /><span>Find who comes next.</span></h1><p>Enter your company and round, confirm the Cala matches,<br className="desktop-break" /> and turn the lead's network into an introduction request.</p></div><div className="hero-note"><ShieldCheck size={17} /><span>Cala facts.<br /><strong>Your decision.</strong></span></div></div>
        <div className="inline-note" role="note"><CircleHelp size={15} /><p><strong>Cala testing profile.</strong> The prefilled seed round and amounts are hypothetical. Leave the lead blank to look for a historical lead in Cala, then confirm the right investor.</p></div>
        <div className="workspace-grid"><form className="profile-form" onSubmit={resolve}>
          <section className="form-section"><div className="section-heading"><span className="section-number mono">01</span><div><h2>Your company</h2><p>Start with your company and sector.</p></div></div>
            <div className="form-grid"><Field title="Founder name" optional><input maxLength={200} value={form.founder_name} onChange={event => update("founder_name", event.target.value)} autoComplete="name" /></Field>
              <Field title="Company name"><input required maxLength={200} value={form.company_name} onChange={event => update("company_name", event.target.value)} placeholder="Your company" autoComplete="organization" /></Field>
              <Field title="Company website" optional hint="A domain such as cala.ai is enough."><input value={form.website} onChange={event => update("website", event.target.value)} placeholder="cala.ai" autoComplete="url" /></Field>
              <Field title="Sector" hint="Comma-separated terms, such as AI infrastructure, data."><input required maxLength={200} value={form.sector} onChange={event => update("sector", event.target.value)} placeholder="AI infrastructure, data" /></Field>
              <Field title="Headquarters country" optional><input maxLength={100} value={form.country} onChange={event => update("country", event.target.value)} placeholder="Country, if known" autoComplete="country-name" /></Field>
              <Field title="Round stage"><select required value={form.stage} onChange={event => update("stage", event.target.value as FormData["stage"])}><option value="pre_seed">Pre-seed</option><option value="seed">Seed</option><option value="series_a">Series A</option><option value="series_b">Series B</option></select></Field></div>
            <label className="check-row lookup-check"><input type="checkbox" checked={lookupStartup} onChange={event => setLookupStartup(event.target.checked)} /><span>Also look up my company in Cala<small>A company match can help find your lead. You confirm every match.</small></span></label>
          </section>
          <section className="form-section"><div className="section-heading"><span className="section-number mono">02</span><div><h2>The round</h2><p>How much are you raising, and how much is already committed?</p></div></div>
            <div className="form-grid"><Field title="Round target (EUR)" hint="Numbers, K or M; for example 4M."><input required inputMode="decimal" value={form.target_eur} onChange={event => update("target_eur", event.target.value)} placeholder="4M" /></Field>
              <Field title="Already committed (EUR)" hint="Firm commitments, excluding conditional interest."><input required inputMode="decimal" value={form.firm_commitments_eur} onChange={event => update("firm_commitments_eur", event.target.value)} placeholder="2M" /></Field></div>
          </section>
          <section className="form-section"><div className="section-heading"><span className="section-number mono">03</span><div><h2>Your lead & existing investors</h2><p>Use your lead's name, or let Cala suggest one for you to confirm.</p></div></div>
            <div className="form-grid"><Field title="Lead investor name" optional className="span-two" hint="Leave blank to look for a lead in your company's funding history."><input maxLength={200} value={form.lead_name} onChange={event => update("lead_name", event.target.value)} placeholder="Leave blank to auto-detect via Cala" /></Field>
              <Field title="Investors already in (excluded)" className="span-two" optional hint="Separate names with commas. These investors are excluded from suggestions."><input value={form.excluded_investors} onChange={event => update("excluded_investors", event.target.value)} placeholder="Investor names" /></Field>
              <Field title="Known competitors" className="span-two" optional hint="Separate company names with commas."><input value={form.known_competitors} onChange={event => update("known_competitors", event.target.value)} placeholder="Company names" /></Field></div>
          </section>
          <details className="advanced-controls"><summary>Additional relationship controls<ChevronDown size={14} /></summary><div className="advanced-controls-content">
            <Field title="Lead commitment status"><select value={form.lead_status} onChange={event => update("lead_status", event.target.value as FormData["lead_status"])}><option value="unconfirmed">Current-round commitment unconfirmed</option><option value="signed">Signed</option><option value="firm_verbal">Firm verbal commitment</option><option value="conditional">Conditional interest</option></select></Field>
            {form.lead_status === "conditional" && <div className="inline-note"><CircleHelp size={15} /><p>Results and introduction requests will describe this lead commitment as provisional.</p></div>}
            <div className="status-rows">{form.statuses.map((row, index) => <div className="status-row" key={index}>
              <input aria-label={`Investor ${index + 1} name`} required value={row.investor_name} placeholder="Investor name" onChange={event => update("statuses", form.statuses.map((item, idx) => idx === index ? { ...item, investor_name: event.target.value, investor_id: undefined } : item))} />
              <select aria-label={`Investor ${index + 1} status`} value={row.status} onChange={event => update("statuses", form.statuses.map((item, idx) => idx === index ? { ...item, status: event.target.value as InvestorStatus } : item))}>{Object.entries(STATUS_LABELS).map(([value, name]) => <option value={value} key={value}>{name}</option>)}</select>
              <button type="button" className="remove-button" aria-label={`Remove investor ${index + 1}`} onClick={() => update("statuses", form.statuses.filter((_, idx) => idx !== index))}><X size={15} /></button></div>)}</div>
            <button type="button" className="button text-button add-status" onClick={() => update("statuses", [...form.statuses, { investor_name: "", status: "existing_shareholder" }])}><Plus size={14} />Add an investor status</button>
            <div className="preference-options"><label className="check-row"><input type="checkbox" checked={form.include_angels} onChange={event => update("include_angels", event.target.checked)} /><span>Include angels</span></label><label className="check-row"><input type="checkbox" checked={form.include_corporate_vcs} onChange={event => update("include_corporate_vcs", event.target.checked)} /><span>Include corporate VCs</span></label></div>
            <Field title="Investor countries" optional hint="Leave blank for all countries. Separate countries with commas."><input value={form.investor_countries} onChange={event => update("investor_countries", event.target.value)} placeholder="Finland, Sweden, United Kingdom" /></Field>
          </div></details>
          {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>{error}</span></div>}
          <div className="form-footer"><span><ShieldCheck size={14} />You review every request.</span><button className="button primary large" disabled={busy} type="submit">{busy ? <LoaderCircle size={16} className="spinning" /> : <Network size={16} />}{busy ? "Matching in Cala…" : "Match entities in Cala"}<ArrowRight size={16} /></button></div>
        </form>
        <aside className="guidance-sidebar"><div className="guidance-card"><span className="eyebrow">A connection, with context</span><NetworkGraphic /><h3>Start with the network<br />you already have.</h3><p>Confirm your company and lead, then explore their sourced funding connections.</p><div className="guidance-divider" /><ul className="benefit-list"><li><GitBranch size={16} /><span>Shared rounds, backed by Cala</span></li><li><FileCheck2 size={16} /><span>Fit and allocation estimates</span></li><li><Copy size={16} /><span>A request ready to copy</span></li></ul></div>
          <div className="allocation-preview"><span className="eyebrow">Your allocation preview</span><strong>{previewGap == null ? "—" : money(previewGap)}</strong><span>Remaining after firm commitments</span></div>
          <p className="sidebar-footnote"><CircleHelp size={14} />You choose what to ask, and who to approach.</p></aside></div></>}

      {phase === "confirm" && resolution && <><button className="back-link" onClick={editProfile}><ArrowLeft size={14} />Edit your round</button><div className="hero compact"><div><h1>Let&apos;s make sure<br /><span>we have the right lead.</span></h1><p>Your confirmation starts the search. Your startup profile stays in your hands.</p></div></div>
        <div className="workspace-grid"><div className="confirmation-main"><section className="confirmation-section"><div className="section-heading"><span className="section-number mono">01</span><div><h2>{resolution.no_open_allocation ? "No open allocation" : "Choose your lead"}</h2><p>{resolution.no_open_allocation ? "Firm commitments already meet or exceed your round target." : `Cala matches for ${resolution.profile.lead_name || form.lead_name || "your lead"}`}</p></div></div>
          {resolution.detected_lead && <div className="inline-note detected-lead-note" role="note"><CircleHelp size={15} /><div><p><strong>Lead suggested from funding history.</strong> {resolution.detected_lead.lead.name} appears in {resolution.startup_matches.find(item => item.id === resolution.detected_lead?.company_id)?.name || resolution.profile.startup.company_name}'s {resolution.detected_lead.stage ? label(resolution.detected_lead.stage) : "funding"} round{resolution.detected_lead.event_date ? ` (${dateLabel(resolution.detected_lead.event_date, resolution.detected_lead.event_date_precision)})` : ""}. Confirm that this is the lead you want to use; historical participation does not establish a current-round commitment.</p><Sources sources={resolution.detected_lead.sources} title="detection sources" /></div></div>}
          <div className="entity-options">{resolution.lead_matches.map(entity => <EntityOption key={entity.id} entity={entity} selected={selectedLead === entity.id} onSelect={() => { setSelectedLead(entity.id); setConfirmed(false); }} />)}</div>
          {!resolution.lead_matches.length && !resolution.no_open_allocation && <div className="empty-inline"><Network size={24} /><h3>No lead match found</h3><p>Try the investor&apos;s full organization name. You&apos;ll need a confirmed Cala lead to search their network.</p><button className="button secondary" onClick={editProfile}><ArrowLeft size={14} />Refine the name</button></div>}
          <small className="resolver-time mono">ENTITY LOOKUP · {(resolution.resolver_ms / 1000).toFixed(1)}s · not included in run time</small></section>
          <section className="confirmation-section"><div className="section-heading"><span className="section-number mono">02</span><div><h2>Confirm your startup profile</h2><p>Your manual profile works even if your company is absent from Cala.</p></div></div>
            <div className="profile-summary"><div><strong>{resolution.profile.startup.company_name}</strong><span>{resolution.profile.startup.sector}{resolution.profile.startup.country ? ` / ${resolution.profile.startup.country}` : ""}</span></div>{resolution.profile.startup.website && <p>{new URL(resolution.profile.startup.website).hostname}</p>}<dl><div><dt>Round stage</dt><dd>{label(resolution.profile.round.stage)}</dd></div><div><dt>Target / committed</dt><dd>{money(resolution.profile.round.target_eur)} / {money(resolution.profile.round.firm_commitments_eur)}</dd></div></dl></div>
            {resolution.startup_matches.length > 0 && <div className="startup-matches"><h4>Optional Cala company match</h4><label className="manual-option"><input type="radio" name="startup-match" checked={selectedStartup === null} onChange={() => setSelectedStartup(null)} />Use my manual profile only</label>{resolution.startup_matches.map(entity => <EntityOption key={entity.id} entity={entity} selected={selectedStartup === entity.id} onSelect={() => setSelectedStartup(entity.id)} />)}<p className="field-hint">A match records the company identity. The startup details above remain the founder-approved profile.</p></div>}
          </section>
          {detectedCompanyMismatch && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>This lead was suggested from a different company match. Choose the company used for detection, or edit your round and enter the lead explicitly.</span></div>}
          {resolution.notices.map((notice, idx) => <div className="inline-note" key={idx}><CircleHelp size={15} /><p>{notice}</p></div>)}
          <label className="check-row confirm-check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>I confirm this is the correct lead and startup profile.</span></label>
          {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><span>{error}</span></div>}
          <div className="form-footer"><span><Clock3 size={14} />Up to 90 seconds after confirmation</span><button type="button" className="button primary large" disabled={!confirmed || busy || detectedCompanyMismatch || (!selectedLead && !resolution.no_open_allocation)} onClick={() => void beginRun()}>{busy ? <LoaderCircle size={16} className="spinning" /> : <ArrowRight size={16} />}{resolution.no_open_allocation ? "Confirm round summary" : "Find my co-investors"}</button></div></div>
          <aside className="guidance-sidebar"><div className="guidance-card"><span className="eyebrow">Before we begin</span><NetworkGraphic /><h3>The right entity.<br />A stronger starting point.</h3><p>Check the organization&apos;s website, country and description. Similar names can belong to different investors.</p><div className="guidance-divider" /><p>Missing public company details won&apos;t hold you back. We use your manual startup profile as the context for fit.</p></div></aside></div></>}

      {phase === "running" && <><div className="hero compact"><div><span className="running-label"><span className="live-dot" />Search in progress</span><h1>Following the connections.<br /><span>Building your shortlist.</span></h1><p>Retrieving shared rounds, reviewing investor context, and preparing your next ask.</p></div><div className="timer"><strong className="mono">{String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(elapsed % 60).padStart(2, "0")}</strong><span>of a 90-second target</span></div></div>
        <div className="progress-steps">{STEPS.map((step, idx) => <div key={step} className={idx === activeStep ? "current" : idx < activeStep ? "done" : ""}><span>{idx < activeStep ? <Check size={13} /> : idx === activeStep ? <LoaderCircle size={13} className="spinning" /> : String(idx + 1).padStart(2, "0")}</span><strong>{step}</strong></div>)}</div>
        <div className="workspace-grid progress-grid"><div><Trace events={events} live />{streamNotice && <div className="inline-note"><Radio size={15} /><p>The live trace is reconnecting. Result polling is still active.</p></div>}{error && <div className="error-banner" role="alert"><CircleHelp size={15} /><span>{error}</span></div>}{recoveryAvailable && <div className="recovery-actions" role="status"><p>The result is taking longer than expected, or this run is no longer available. You can return to your profile and start a fresh search.</p><button type="button" className="button secondary" onClick={() => { setRun(null); editProfile(); }}><ArrowLeft size={14} />Return to your profile</button></div>}</div><aside className="guidance-card progress-aside"><div className="dot-loader" aria-hidden="true">{Array.from({ length: 25 }, (_, idx) => <i key={idx} style={{ animationDelay: `${(idx % 5 + Math.floor(idx / 5)) * .12}s` }} />)}</div><h3>A useful shortlist<br />can be a short one.</h3><p>We return up to five investors with verified shared rounds. When evidence is sparse, you&apos;ll see fewer results and clear gaps.</p><div className="guidance-divider" /><p className="small-muted">The timer started with your confirmation. Unfinished profiles remain incomplete; they are never silently rejected.</p></aside></div></>}

      {phase === "results" && result && <ResultsWorkspace key={result.run_id} result={result} busy={busy} error={error}
        onEdit={editProfile} onRerun={() => void beginRun(result.profile)}
        renderInvestorDetails={(card, index) => <Candidate card={card} index={index} initiallyExpanded scoringVersion={result.scoring_version} busy={busy} onExclude={candidate => void exclude(candidate)} />}
        renderSources={sources => <Sources sources={sources} title="sources" />} trace={<Trace events={events} />} />}
      <footer className="page-footer"><span>roundcloser</span><p>Connections backed by Cala. Introductions guided by you.</p><span className="mono">MVP · EUR</span></footer>
    </div></main></div>;
}

function EntityOption({ entity, selected, onSelect }: { entity: Entity; selected: boolean; onSelect: () => void }) {
  return <div className={`entity-option ${selected ? "selected" : ""}`}><button type="button" className="entity-choice" onClick={onSelect} aria-pressed={selected}>
    <span className="selection-circle">{selected && <Check size={13} />}</span><div><strong>{entity.name}</strong><EntityIdentity entity={entity} />{entity.description && <p>{entity.description}</p>}</div></button>
    <div className="entity-evidence">{entity.website && <a href={entity.website} target="_blank" rel="noopener noreferrer">{new URL(entity.website).hostname}<ExternalLink size={11} /></a>}<Sources sources={entity.sources} title="sources" /><small className="mono">{entity.id}</small></div><EntityMetadata entity={entity} /></div>;
}
