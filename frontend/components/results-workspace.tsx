"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Copy, FileCheck2, FileText, Network, RotateCcw, X } from "lucide-react";
import type { CandidateCard, RunResult, Source } from "../lib/types";
import AgentInsights from "./agent-insights";
import ResultsGraph from "./results-graph";
import AllocationSummary, { InvestorAllocation } from "./allocation-panel";
import { ACTION_LABELS, ORIGIN_LABELS } from "../lib/trust-labels";
import { RankingGuide } from "./score-explainer";
import RunReportDialog from "./run-report";
import type { ReportTurn } from "../lib/run-report";

interface Props {
  result: RunResult;
  busy: boolean;
  error: string | null;
  onEdit: () => void;
  onRerun: () => void;
  renderInvestorDetails: (card: CandidateCard, index: number) => ReactNode;
  renderSources: (sources: Source[]) => ReactNode;
  trace: ReactNode;
}

const money = (value: string | undefined) => value === undefined ? "Unknown" : new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", maximumFractionDigits: 2,
}).format(Number(value));

function CopyRequest({ text, label, compact = false }: { text: string; label: string; compact?: boolean }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true); setError(false);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch { setError(true); }
  }
  return <span className="workspace-copy"><button type="button" className={`button ${compact ? "workspace-copy-icon" : "secondary"}`} aria-label={copied ? `${label}: copied` : label} title={label} onClick={() => void copy()}>
    {copied ? <CheckCheck size={14} /> : <Copy size={14} />}{!compact && (copied ? "Copied" : label)}
  </button>{error && <small role="status">Open the details to select and copy the draft.</small>}</span>;
}

function DetailsDialog({ card, children, onClose }: { card: CandidateCard; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);
  return <dialog className="investor-dialog" ref={ref} aria-labelledby={titleId} onCancel={onClose}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="investor-dialog-heading"><div><span className="eyebrow">Evidence & next steps</span><h2 id={titleId}>{card.investor.name}</h2></div>
      <button className="button workspace-icon-button" type="button" aria-label="Close investor details" onClick={onClose}><X size={19} /></button></div>
    <div className="investor-dialog-body">{children}</div>
  </dialog>;
}

export default function ResultsWorkspace({ result, busy, error, onEdit, onRerun, renderInvestorDetails, renderSources, trace }: Props) {
  const [selectedInvestorId, setSelectedInvestorId] = useState<string | null>(() => result.candidates[0]?.investor.id ?? null);
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const [combinedOpen, setCombinedOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [turns, setTurns] = useState<ReportTurn[]>([]);
  const detailIndex = result.candidates.findIndex(card => card.investor.id === detailsId);
  const detailCard = result.candidates[detailIndex];
  const count = result.candidates.length;
  const rules = result.scoring_version.startsWith("rules-");
  const provisional = result.profile.round.lead_status === "conditional" || result.profile.round.lead_status === "unconfirmed";
  const eligibleCount = result.candidates.filter(card => card.review_status !== "flagged").length;
  const displayError = error || (result.error && String(result.error.message || "Search unavailable"));
  const heading = count ? "Your introduction workspace." : result.state === "no_open_allocation"
    ? "Your round has no open allocation." : result.state === "failed" ? "The search couldn’t finish." : "No connections to recommend yet.";

  useEffect(() => {
    setSelectedInvestorId(result.candidates[0]?.investor.id ?? null);
    setDetailsId(null); setCombinedOpen(false); setReportOpen(false); setTurns([]);
  }, [result.run_id]);

  function showDetails(id: string) { setSelectedInvestorId(id); setDetailsId(id); }

  return <div className="results-workspace-view">
    <div className="results-top workspace-top"><button className="back-link" onClick={onEdit}><ArrowLeft size={14} />Adjust your round</button>
      <div className="workspace-top-actions"><button type="button" className="button primary" disabled={busy} onClick={() => setReportOpen(true)}
          title="Summary of this shortlist and your chat, with next actions"><FileText size={14} />Generate report</button>
        {result.combined_draft && <CopyRequest text={result.combined_draft} label={`Copy ${Math.min(3, eligibleCount)}-investor request`} />}
        <button type="button" className="button text-button" disabled={busy} onClick={onRerun}><RotateCcw size={13} />Rerun search</button></div></div>
    <div className="hero workspace-hero"><h1>{heading}</h1></div>
    <div className="summary-strip workspace-summary"><div><span>Remaining allocation</span><strong>{money(result.remaining_allocation_eur)}</strong></div>
      <div><span>Firm commitments</span><strong>{money(result.profile.round.firm_commitments_eur)}</strong></div>
      <div><span>Round target</span><strong>{money(result.profile.round.target_eur)}</strong></div>
      <div><span>Lead commitment</span><strong className="text-metric">{result.profile.round.lead_status === "unconfirmed" ? "Unconfirmed" : provisional ? "Provisional" : result.profile.round.lead_status.replaceAll("_", " ")}</strong></div></div>
    <div className="result-meta"><span>{result.counts.discovered ?? 0} discovered · {result.counts.profiled ?? 0} profiled · {eligibleCount} ready · {count - eligibleCount} need review · {result.counts.excluded ?? 0} excluded · {result.counts.incomplete ?? 0} incomplete</span>
      <span className="mono">{(result.processing_ms / 1000).toFixed(1)}s · {result.cala_calls} Cala calls · {result.cache_hits} cache hits</span></div>
    {displayError && <div className="error-banner" role="alert"><CircleHelp size={15} /><span>{displayError}</span></div>}

    <div className="result-workspace">
      <section className="workspace-shortlist" aria-labelledby="workspace-shortlist-heading">
        <div className="shortlist-heading workspace-panel-heading"><div><h2 id="workspace-shortlist-heading">Your top investors</h2><p>{rules ? "Rules met" : "Scores"} and estimated allocation · {money(result.remaining_allocation_eur)} open</p></div><span className="workspace-count mono">{count} / 5</span></div>
        <RankingGuide result={result} />
        <div className="workspace-investor-list">{result.candidates.map((card, index) => <div key={card.investor.id} className={`workspace-investor-row ${selectedInvestorId === card.investor.id ? "is-selected" : ""}`}>
          <button type="button" className="workspace-investor-select" aria-label={`Select ${card.investor.name}`} aria-pressed={selectedInvestorId === card.investor.id} onClick={() => setSelectedInvestorId(card.investor.id)}>
            <span className="workspace-rank mono">{String(index + 1).padStart(2, "0")}</span><span className="workspace-investor-name"><strong title={card.investor.name}>{card.investor.name}</strong>{card.review_status === "flagged" && <span className="workspace-review-status">Needs review</span>}
              {(card.origins || []).filter(origin => origin !== "lead_network").map(origin => <span key={origin} className={`workspace-origin-chip origin-${origin}`}>{ORIGIN_LABELS[origin]}</span>)}
              <small>{card.shared_round_count} shared {card.shared_round_count === 1 ? "round" : "rounds"}{rules ? "" : ` · ${Math.round(card.profile_completeness * 100)}% complete`}{card.action === "follow_up" ? " · Follow-up" : ""}</small>
              {card.recommended_action && <span className={`workspace-action-chip action-${card.recommended_action}`}>
                {ACTION_LABELS[card.recommended_action]}{card.action_contact ? ` · ${card.action_contact.name}` : ""}</span>}</span>
            {rules ? <span className="workspace-investor-score workspace-rules-score" title="Number of ranking rules this investor meets">{card.fit_score}<small>{card.fit_score === 1 ? "rule met" : "rules met"}</small></span>
              : <span className="workspace-investor-score" title="Introduction priority, not an investment probability">{card.fit_score}<small>/100</small></span>}</button>
          <CopyRequest text={card.draft} label={`Copy request for ${card.investor.name}`} compact />
          <button type="button" className="button workspace-icon-button" aria-label={`View details for ${card.investor.name}`} title="Evidence, fit & draft" onClick={() => showDetails(card.investor.id)}><ChevronRight size={17} /></button>
          <InvestorAllocation card={card} allocation={result.allocation} />
        </div>)}</div>
        <AllocationSummary result={result} />
        {!count && <div className="workspace-empty-shortlist"><Network size={25} /><h3>{result.state === "no_open_allocation" ? "Allocation is filled" : "No completed investor cards"}</h3>
          <p>{result.state === "no_open_allocation" ? "Firm commitments meet or exceed the target. Adjust your round if the allocation changes." : "Investors appear here when Cala identifies a shared round with your lead or a round in your company or a founder's previous company. Review the scope and exclusions or adjust your profile."}</p><button type="button" className="button text-button" onClick={onEdit}>Adjust profile<ChevronRight size={13} /></button></div>}
        <footer className="workspace-shortlist-footer"><span><FileCheck2 size={13} />Select to explore · open details for sources</span>
          {result.combined_draft && <button type="button" className="button text-button" onClick={() => setCombinedOpen(!combinedOpen)} aria-expanded={combinedOpen}>Combined request<ChevronDown size={13} /></button>}</footer>
        {combinedOpen && <div className="workspace-combined"><div><h3>Your combined request</h3><button type="button" className="button workspace-icon-button" aria-label="Close combined request" onClick={() => setCombinedOpen(false)}><X size={15} /></button></div><pre tabIndex={0}>{result.combined_draft}</pre><CopyRequest text={result.combined_draft} label="Copy combined request" /></div>}
      </section>
      <div className="workspace-chat-panel"><AgentInsights key={result.run_id} result={result} selectedInvestorId={selectedInvestorId} onSelectInvestor={setSelectedInvestorId} trace={trace} onConversationChange={setTurns} /></div>
      <div className="workspace-graph-panel"><ResultsGraph result={result} selectedInvestorId={selectedInvestorId} onSelectInvestor={setSelectedInvestorId} /></div>
    </div>

    {(result.exclusions.length > 0 || result.incomplete.length > 0) && <div className="workspace-extra-results">
      {result.incomplete.length > 0 && <section className="supplementary"><h3>Incomplete profiles <span>{result.incomplete.length}</span></h3><p>These investors were not rejected; profiling did not finish in this run.</p>{result.incomplete.map(entity => <div className="supplementary-row" key={entity.id}><span>{entity.name}</span><span className="chip">Incomplete</span></div>)}</section>}
      {result.exclusions.length > 0 && <section className="supplementary"><h3>Excluded from this shortlist <span>{result.exclusions.length}</span></h3><p>Explicit founder rules or applicable published restrictions.</p>{result.exclusions.map((item, index) => <div className="exclusion-row" key={`${item.investor.id}-${index}`}><strong>{item.investor.name}</strong><p>{item.reason}</p>{renderSources(item.sources)}</div>)}</section>}
    </div>}
    {reportOpen && <RunReportDialog result={result} turns={turns} onClose={() => setReportOpen(false)} />}
    {detailCard && <DetailsDialog key={detailCard.investor.id} card={detailCard} onClose={() => setDetailsId(null)}>{renderInvestorDetails(detailCard, detailIndex)}</DetailsDialog>}
  </div>;
}
