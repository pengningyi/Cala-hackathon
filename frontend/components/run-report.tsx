"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCheck, ChevronDown, ClipboardList, Copy, Download, ExternalLink, ListChecks, LoaderCircle, MessageCircle, RotateCcw, Sparkles, Users, X } from "lucide-react";
import type { RunResult } from "../lib/types";
import { buildRunReport, reportDate, reportFilename, reportMarkdown, type ReportTurn, type RunReport } from "../lib/run-report";
import { generateRunReport, getRunEvidenceStatus, type RunChatResponse } from "../lib/chat-api";
import ChatMarkdown from "./chat-markdown";

export interface SavedRunReport {
  runId: string;
  report: RunReport;
  narrative: RunChatResponse;
}

interface Props {
  result: RunResult;
  turns: ReportTurn[];
  savedReport: SavedRunReport | null;
  latestEvidenceRevision: number;
  onSaved: (report: SavedRunReport) => void;
  onEvidenceRevision: (revision: number) => void;
  onClose: () => void;
}

/** Saved shortlist facts stay available while the report assistant prepares a separate narrative. */
export default function RunReportDialog({ result, turns, savedReport, latestEvidenceRevision, onSaved, onEvidenceRevision, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  // Preserve the exact successful snapshot across closing and reopening the dialog.
  const [snapshot] = useState(() => ({ runId: result.run_id, report: buildRunReport(result, turns),
    history: turns.slice(-8).map(turn => ({ role: turn.role, content: turn.content.slice(0, 4000) })) }));
  const [saved, setSaved] = useState(savedReport);
  const report = saved?.report ?? snapshot.report;
  const narrative = saved?.narrative ?? null;
  const [loading, setLoading] = useState(!savedReport);
  const [error, setError] = useState<string | null>(null);
  const markdown = useMemo(() => reportMarkdown(report, narrative), [report, narrative]);
  const stale = narrative !== null && latestEvidenceRevision > narrative.evidence_revision;
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [caveatsOpen, setCaveatsOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const lifetimeController = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const listeners = useRef({ onSaved, onEvidenceRevision });
  listeners.current = { onSaved, onEvidenceRevision };

  async function refreshEvidence(signal: AbortSignal) {
    try {
      const status = await getRunEvidenceStatus(snapshot.runId, signal);
      if (!signal.aborted) listeners.current.onEvidenceRevision(status.revision);
    } catch { /* Older/offline backends cannot refresh; completed chat revisions still work. */ }
  }

  async function generate(nextReport: RunReport, history: typeof snapshot.history) {
    if (requestController.current) return;
    const controller = new AbortController();
    requestController.current = controller;
    const version = ++requestVersion.current;
    setLoading(true); setError(null);
    try {
      const reply = await generateRunReport(snapshot.runId, history, controller.signal);
      if (controller.signal.aborted || version !== requestVersion.current) return;
      const updated = { runId: snapshot.runId, report: nextReport, narrative: reply };
      setSaved(updated);
      listeners.current.onSaved(updated);
      // Use the server's exact included revision, then check for evidence that arrived in flight.
      listeners.current.onEvidenceRevision(reply.evidence_revision);
      if (lifetimeController.current) void refreshEvidence(lifetimeController.current.signal);
    } catch {
      if (!controller.signal.aborted && version === requestVersion.current) {
        setError(saved
          ? "The report could not be updated. Your previous report is still available to copy or download."
          : "Founder analysis is unavailable right now. You can still copy or download the report snapshot.");
      }
    } finally {
      if (version === requestVersion.current) { setLoading(false); requestController.current = null; }
    }
  }

  function update() {
    void generate(buildRunReport(result, turns), turns.slice(-8).map(turn => ({ role: turn.role, content: turn.content.slice(0, 4000) })));
  }

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => { if (timer.current) clearTimeout(timer.current); if (dialog?.open) dialog.close(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    lifetimeController.current = controller;
    // StrictMode replays effects before this task runs; cancel the first scheduled request.
    const start = setTimeout(() => {
      void refreshEvidence(controller.signal);
      if (!savedReport) void generate(snapshot.report, snapshot.history);
    }, 0);
    return () => {
      clearTimeout(start); controller.abort(); requestVersion.current += 1;
      requestController.current?.abort(); requestController.current = null;
    };
    // Each opening captures one initial snapshot; further generation requires an explicit update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true); setCopyFailed(false);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch { setCopyFailed(true); }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = reportFilename(report);
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return <dialog className="investor-dialog run-report-dialog" ref={ref} aria-labelledby={titleId} onCancel={onClose}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="investor-dialog-heading">
      <div><span className="eyebrow">{report.company} · {reportDate(report.generatedAt)}</span><h2 id={titleId}>Round report</h2></div>
      <div className="run-report-tools">
        <button type="button" className="button secondary" aria-label={copied ? "Copy report as Markdown: copied" : "Copy report as Markdown"} onClick={() => void copy()}>
          {copied ? <CheckCheck size={14} /> : <Copy size={14} />}{copied ? "Copied" : "Copy"}</button>
        <button type="button" className="button secondary" onClick={download}><Download size={14} />Download .md</button>
        <button className="button workspace-icon-button" type="button" aria-label="Close report" onClick={onClose}><X size={19} /></button>
      </div>
    </div>
    {copyFailed && <p className="run-report-copy-failed" role="status">Copying is blocked in this browser. Use Download .md instead.</p>}
    {stale && <div className="run-report-update" role="status"><span>New findings available. Update your report to include them.</span>
      <button type="button" className="button secondary" disabled={loading} onClick={update}><RotateCcw size={13} />Update report</button></div>}

    <div className="run-report-body">
      <section aria-labelledby={`${titleId}-summary`}>
        <h3 id={`${titleId}-summary`}><ClipboardList size={14} />Summary</h3>
        <p className="run-report-summary">{report.summary}</p>
        <dl className="run-report-round">{report.round.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
      </section>

      <section className="run-report-narrative" aria-labelledby={`${titleId}-analysis`} aria-busy={loading}>
        <h3 id={`${titleId}-analysis`}><Sparkles size={14} />Founder analysis</h3>
        {loading && <p className="run-report-muted run-report-progress" role="status"><LoaderCircle size={14} className="spinning" />{saved
          ? "Updating founder analysis. Your previous report is still available to copy or download."
          : "Preparing an analysis from your saved results and conversation. The snapshot is ready to copy or download."}</p>}
        {error && <div className="run-report-error" role="alert"><p>{error}</p>
          <button type="button" className="button secondary" disabled={loading} onClick={update}><RotateCcw size={13} />{saved ? "Retry update" : "Retry analysis"}</button></div>}
        {narrative && <>
          <ChatMarkdown content={narrative.message} citations={narrative.citations} citationIds={narrative.citation_ids} />
          {narrative.citations.length > 0 && <ol className="run-report-sources" aria-label="Founder analysis sources">{narrative.citations.map((source, index) => <li key={`${source.url}-${index}`}>
            <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label || new URL(source.url).hostname}<ExternalLink size={11} /></a>
          </li>)}</ol>}
        </>}
      </section>

      <section className="run-report-actions" aria-labelledby={`${titleId}-actions`}>
        <h3 id={`${titleId}-actions`}><ListChecks size={14} />Key next actions</h3>
        {report.actions.length
          ? <ol>{report.actions.map(action => <li key={action.text}>{action.text}<small>{action.source}</small></li>)}</ol>
          : <p className="run-report-muted">No actions yet. Ask the round assistant how to approach your shortlist.</p>}
      </section>

      <section aria-labelledby={`${titleId}-investors`}>
        <h3 id={`${titleId}-investors`}><Users size={14} />Top investors</h3>
        {report.investors.length
          ? <ul className="run-report-investors">{report.investors.map(investor => <li key={investor.id}>
            <div><span className="mono">{String(investor.rank).padStart(2, "0")}</span><strong>{investor.name}</strong><span className="mono">{investor.score}</span><em>{investor.action}</em></div>
            {investor.why && <p>{investor.why}</p>}
            {investor.detail && <small>{investor.detail}</small>}
          </li>)}</ul>
          : <p className="run-report-muted">No investors on this shortlist.</p>}
      </section>

      <section aria-labelledby={`${titleId}-chat`}>
        <h3 id={`${titleId}-chat`}><MessageCircle size={14} />Conversation highlights</h3>
        {report.exchanges.length
          ? <ul className="run-report-exchanges">{report.exchanges.map((exchange, index) => <li key={index}>
            <span className="run-report-q">{exchange.question}</span>
            <p>{exchange.answer}</p>
            {exchange.investors.length > 0 && <small>Investors discussed: {exchange.investors.join(", ")}</small>}
          </li>)}</ul>
          : <p className="run-report-muted">No questions asked yet. Highlights from the round assistant appear here.</p>}
      </section>

      <section>
        <h3><button type="button" className="run-report-caveats-toggle" aria-expanded={caveatsOpen} aria-controls={`${titleId}-caveats`}
          onClick={() => setCaveatsOpen(open => !open)}><AlertTriangle size={14} />Caveats<span>{report.caveats.length}</span>
          <ChevronDown size={14} className="run-report-chevron" /></button></h3>
        <ul id={`${titleId}-caveats`} className="run-report-caveats" hidden={!caveatsOpen}>{report.caveats.map(caveat => <li key={caveat}>{caveat}</li>)}</ul>
      </section>
    </div>
  </dialog>;
}
