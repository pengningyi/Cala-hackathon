"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronDown, ExternalLink, FileCheck2, Radio, X } from "lucide-react";
import type { RunResult, Source } from "../lib/types";

interface Props {
  result: RunResult;
  selectedInvestorId: string | null;
  onSelectInvestor: (id: string | null) => void;
  trace: ReactNode;
}

type Tab = "understanding" | "activity";
const money = (value: string) => new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", maximumFractionDigits: 2,
}).format(Number(value));

function dateLabel(value: string | null) {
  if (!value) return "Unknown";
  const parsed = new Date(value.includes("T") ? value : `${value}T12:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? "Unknown" : new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  }).format(parsed);
}

function Evidence({ sources }: { sources: Source[] }) {
  const distinct = sources.filter((source, index) => sources.findIndex(item => item.url === source.url
    && item.published_at === source.published_at && item.retrieved_at === source.retrieved_at) === index);
  if (!distinct.length) return null;
  return <details className="agent-insight-evidence"><summary>Sources for the selected investor<ChevronDown size={12} /></summary>
    <ul>{distinct.slice(0, 6).map((source, index) => <li key={`${source.url}-${index}`}>
      <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label || new URL(source.url).hostname}<ExternalLink size={11} /></a>
      <span>Published {dateLabel(source.published_at)} · retrieved {dateLabel(source.retrieved_at)}</span>
    </li>)}</ul>{distinct.length > 6 && <p>More source links are available in investor details.</p>}</details>;
}

/** Main's current-understanding and activity views, using the stored typed run. */
export default function AgentInsights({ result, selectedInvestorId, onSelectInvestor, trace }: Props) {
  const [tab, setTab] = useState<Tab>("understanding");
  const id = useId();
  const understandingTab = useRef<HTMLButtonElement>(null);
  const activityTab = useRef<HTMLButtonElement>(null);
  const eligible = result.candidates.filter(card => card.review_status !== "flagged");
  const flagged = result.candidates.filter(card => card.review_status === "flagged");
  const top = eligible[0];
  const selected = result.candidates.find(card => card.investor.id === selectedInvestorId);
  const conflicts = result.candidates.flatMap(card => (card.flags || []).filter(flag => flag.kind === "conflict")
    .map(flag => ({ name: card.investor.name, reason: flag.reason })));
  const reviews = flagged.filter(card => !(card.flags || []).some(flag => flag.kind === "conflict"));
  const unconfirmed = result.profile.round.lead_status === "unconfirmed" || result.profile.round.lead_status === "conditional";

  useEffect(() => { setTab("understanding"); }, [result.run_id]);
  function switchTab(next: Tab) {
    setTab(next);
    (next === "understanding" ? understandingTab : activityTab).current?.focus();
  }
  function keyTabs(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const focused = event.currentTarget === understandingTab.current ? "understanding" : "activity";
    switchTab(event.key === "Home" ? "understanding" : event.key === "End" ? "activity" : focused === "understanding" ? "activity" : "understanding");
  }

  return <section className="agent-insights" aria-label="Round understanding and agent activity">
    <div className="agent-insight-heading"><span className="agent-insight-icon"><FileCheck2 size={19} /></span><div>
      <h2>Your round insights</h2><p>Results, explanations and the calls behind them</p></div></div>
    <div className="agent-insight-tabs" role="tablist" aria-label="Round insights views">
      <button ref={understandingTab} id={`${id}-understanding-tab`} type="button" role="tab" aria-controls={`${id}-understanding`}
        aria-selected={tab === "understanding"} tabIndex={tab === "understanding" ? 0 : -1} onKeyDown={keyTabs}
        onClick={() => setTab("understanding")}><FileCheck2 size={13} />Current understanding</button>
      <button ref={activityTab} id={`${id}-activity-tab`} type="button" role="tab" aria-controls={`${id}-activity`}
        aria-selected={tab === "activity"} tabIndex={tab === "activity" ? 0 : -1} onKeyDown={keyTabs}
        onClick={() => setTab("activity")}><Radio size={13} />Agent activity</button>
    </div>
    {selected && <div className="agent-insight-selection"><span>Selected investor: <strong>{selected.investor.name}</strong></span>
      <button type="button" onClick={() => onSelectInvestor(null)} aria-label="Clear selected investor"><X size={13} /></button></div>}
    <div id={`${id}-understanding`} role="tabpanel" aria-labelledby={`${id}-understanding-tab`} tabIndex={0}
      className="agent-insight-panel agent-insight-understanding" hidden={tab !== "understanding"}>
      {result.state === "no_open_allocation" ? <><h3>Your round has no open allocation.</h3><p>Firm commitments cover the target, so no co-investor search was needed.</p></>
        : !result.candidates.length ? <><h3>No completed investor cards yet.</h3><p>{result.state === "failed" ? "The search could not finish. Review the activity and search notes for the available evidence." : "No eligible lead-connected candidates were completed in the retrieved scope. Review exclusions, incomplete profiles and search coverage."}</p></>
          : <><h3>{eligible.length} shortlisted, {flagged.length} requiring review</h3><p className="agent-insight-open">{money(result.remaining_allocation_eur)} still open</p>
            <ul className="agent-insight-summary">
              {top && <li><button className="agent-insight-investor" type="button" onClick={() => onSelectInvestor(top.investor.id)}>{top.investor.name}</button> ranks first: {top.explanation}</li>}
              <li>{result.counts.profiled ?? result.candidates.length} investor profiles evaluated; {result.candidates.length} candidates displayed from retrieved connections{result.lead ? ` to ${result.lead.name}` : ""}.</li>
              {conflicts.length ? <li>Portfolio-conflict flags need review: {conflicts.slice(0, 3).map(item => `${item.name} — ${item.reason}`).join("; ")}{conflicts.length > 3 ? "; additional flags are available in investor details." : "."}</li>
                : <li>No portfolio-conflict flag was recorded in the displayed profiles; unobserved conflicts remain possible.</li>}
              {reviews.length > 0 && <li>{reviews.length} displayed {reviews.length === 1 ? "investor has" : "investors have"} activity, lead-role or stage flags to review in their details.</li>}
            </ul>
          </>}
      {unconfirmed && <p className="agent-insight-limit">The selected lead&apos;s commitment to this round is {result.profile.round.lead_status === "conditional" ? "provisional" : "unconfirmed"}. Historical connections do not establish introduction access.</p>}
      {result.state === "partial" && <p className="agent-insight-limit">Coverage is partial. Missing information stays unknown.</p>}
      {selected && <div className="agent-insight-selected-explanation"><h4>{selected.investor.name}</h4>
        <p className="agent-insight-score">Score {selected.fit_score}/100 · {Math.round(selected.profile_completeness * 100)}% profile completeness{selected.review_status === "flagged" ? " · Needs review" : ""}</p>
        <p>{selected.explanation}</p>{selected.flags?.length ? <ul>{selected.flags.slice(0, 3).map((flag, index) => <li key={`${flag.kind}-${index}`}>{flag.reason}</li>)}</ul> : null}
        {selected.components.some(component => !component.known) && <p>{result.scoring_version.startsWith("main-") ? "Some scoring inputs are unknown and contribute zero points." : "Some scoring inputs are unknown; review the scoring details before acting."}</p>}
        <Evidence sources={selected.sources} />
      </div>}
    </div>
    <div id={`${id}-activity`} role="tabpanel" aria-labelledby={`${id}-activity-tab`} tabIndex={0}
      className="agent-insight-panel agent-insight-activity" hidden={tab !== "activity"}>{trace}</div>
  </section>;
}
