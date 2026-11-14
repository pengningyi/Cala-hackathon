"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCheck, ChevronDown, ClipboardList, Copy, Download, ListChecks, MessageCircle, Users, X } from "lucide-react";
import type { RunResult } from "../lib/types";
import { buildRunReport, reportDate, reportFilename, reportMarkdown, type ReportTurn } from "../lib/run-report";

interface Props {
  result: RunResult;
  turns: ReportTurn[];
  onClose: () => void;
}

/** A snapshot report of the shortlist and the chat so far, with next actions, copyable as Markdown. */
export default function RunReportDialog({ result, turns, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  // Snapshot when opened: the report does not change while it is being read.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const report = useMemo(() => buildRunReport(result, turns), []);
  const markdown = useMemo(() => reportMarkdown(report), [report]);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [caveatsOpen, setCaveatsOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => { if (timer.current) clearTimeout(timer.current); if (dialog?.open) dialog.close(); };
  }, []);

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

    <div className="run-report-body">
      <section aria-labelledby={`${titleId}-summary`}>
        <h3 id={`${titleId}-summary`}><ClipboardList size={14} />Summary</h3>
        <p className="run-report-summary">{report.summary}</p>
        <dl className="run-report-round">{report.round.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
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
