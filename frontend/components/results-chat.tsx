"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUpRight, Check, ChevronDown, Copy, ExternalLink, Link2, LoaderCircle, MessageCircle, RotateCcw, Send, Sparkles, X } from "lucide-react";
import { askRunChat, type ChatHistoryMessage } from "../lib/chat-api";
import type { RunResult, Source } from "../lib/types";

interface ResultsChatProps {
  result: RunResult;
  selectedInvestorId: string | null;
  onSelectInvestor: (id: string | null) => void;
  embedded?: boolean;
  active?: boolean;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Source[];
  candidateIds: string[];
  origin?: "welcome" | "model" | "evidence";
  failed?: boolean;
}

interface FailedQuestion {
  message: string;
  investorId: string | null;
}

const SUGGESTIONS = ["Why these investors?", "Compare the top two", "Draft an introduction request"];

function welcome(result: RunResult): Message {
  const count = result.candidates.length;
  let content: string;
  if (result.state === "no_open_allocation") {
    content = "Your firm commitments already fill the target, so no co-investor search was needed. I can explain this round summary or help you review what to change in your profile.";
  } else if (!count) {
    content = "There are no completed investor cards in this run. Ask me about the search scope, exclusions or missing information. I can explain the evidence we have, without adding unsupported recommendations.";
  } else {
    const lead = result.lead ? ` through ${result.lead.name}` : "";
    content = `Your shortlist contains ${count} ${count === 1 ? "investor" : "investors"} from your lead's network and your founder network${lead}. Ask me why they appear, compare the options, or work through an introduction request.`;
    if (result.profile.round.lead_status === "conditional") content += " Your lead commitment is provisional.";
    if (result.profile.round.lead_status === "unconfirmed") content += " Your lead commitment is unconfirmed.";
    if (result.state === "partial") content += " Coverage is partial; missing facts stay unknown.";
  }
  return { id: "welcome", role: "assistant", content, citations: [], candidateIds: [], origin: "welcome" };
}

function dateLabel(value: string | null): string {
  if (!value) return "Date unknown";
  const date = new Date(value.includes("T") ? value : `${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return "Date unknown";
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function Citations({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return <details className="rc-chat-citations">
    <summary><Link2 size={12} /><span>{sources.length} {sources.length === 1 ? "source" : "sources"} from this run</span><ChevronDown size={12} /></summary>
    <ol>{sources.map((source, index) => <li key={`${source.url}-${index}`}>
      <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label || new URL(source.url).hostname}<ExternalLink size={11} /></a>
      <span>Published {dateLabel(source.published_at)} · retrieved {dateLabel(source.retrieved_at)}</span>
    </li>)}</ol>
  </details>;
}

function CopyReply({ content }: { content: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      setState("copied");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setState("idle"), 2000);
    } catch { setState("failed"); }
  }
  return <span className="rc-chat-copy-wrap"><button type="button" className="rc-chat-copy" onClick={() => void copy()} aria-label={state === "copied" ? "Reply copied" : "Copy assistant reply"}>
    {state === "copied" ? <Check size={12} /> : <Copy size={12} />}{state === "copied" ? "Copied" : "Copy"}
  </button>{state === "failed" && <span role="status">Select the reply to copy it manually.</span>}</span>;
}

/** A conversation scoped to one immutable run, synchronized with graph selection. */
export default function ResultsChat({ result, selectedInvestorId, onSelectInvestor, embedded = false, active = true }: ResultsChatProps) {
  const [messages, setMessages] = useState<Message[]>(() => [welcome(result)]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedQuestion, setFailedQuestion] = useState<FailedQuestion | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const nextMessage = useRef(1);
  const headingId = useId();
  const inputId = useId();
  const hintId = useId();
  const selected = result.candidates.find(card => card.investor.id === selectedInvestorId);
  const previousRun = useRef(result.run_id);

  useEffect(() => {
    if (previousRun.current !== result.run_id) {
      controllerRef.current?.abort();
      controllerRef.current = null;
      requestVersion.current += 1;
      previousRun.current = result.run_id;
      setMessages([welcome(result)]);
      setInput(""); setBusy(false); setError(null); setFailedQuestion(null);
      nextMessage.current = 1;
    }
  }, [result]);
  useEffect(() => () => { requestVersion.current += 1; controllerRef.current?.abort(); }, []);
  useEffect(() => {
    if (!active) return;
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [messages, busy, error, active]);

  async function send(text: string, investorOverride?: string | null) {
    const question = text.trim();
    if (!question || question.length > 2000 || busy || controllerRef.current) return;
    const base = messages.filter(message => !message.failed);
    const history: ChatHistoryMessage[] = base.filter(message => message.origin !== "welcome")
      .slice(-8).map(message => ({ role: message.role, content: message.content.slice(0, 4000) }));
    const userId = `question-${nextMessage.current++}`;
    const investorId = investorOverride === undefined ? selected?.investor.id ?? null : investorOverride;
    const controller = new AbortController();
    controllerRef.current = controller;
    const version = ++requestVersion.current;
    setMessages([...base, { id: userId, role: "user", content: question, citations: [], candidateIds: [] }]);
    setInput(""); setBusy(true); setError(null); setFailedQuestion(null);
    try {
      const reply = await askRunChat(result.run_id, { message: question, history, selected_investor_id: investorId }, controller.signal);
      if (controller.signal.aborted || version !== requestVersion.current) return;
      setMessages(previous => [...previous, {
        id: `answer-${nextMessage.current++}`, role: "assistant", content: reply.message,
        citations: reply.citations,
        candidateIds: reply.candidate_ids.filter(id => result.candidates.some(card => card.investor.id === id)),
        origin: reply.model_used ? "model" : "evidence",
      }]);
    } catch (failure) {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      setMessages(previous => previous.map(message => message.id === userId ? { ...message, failed: true } : message));
      setInput(question);
      setFailedQuestion({ message: question, investorId });
      setError(failure instanceof Error ? failure.message : "The assistant could not finish. Please try again.");
    } finally {
      if (version === requestVersion.current) { setBusy(false); controllerRef.current = null; }
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send(input);
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(input);
    }
  }

  return <section className={`rc-chat${embedded ? " rc-chat-embedded" : ""}`} aria-labelledby={embedded ? undefined : headingId} aria-label={embedded ? "Chat about your round" : undefined}>
    {!embedded && <header className="rc-chat-header"><div className="rc-chat-heading"><span className="rc-chat-mark"><MessageCircle size={17} /></span>
      <div><h2 id={headingId}>Your round assistant</h2><p>Answers grounded in this run&apos;s evidence</p></div></div><span className="rc-chat-scope"><Link2 size={12} />Cala context</span></header>}
    {selected && <div className="rc-chat-selection"><span>Discussing <strong>{selected.investor.name}</strong></span>
      <button type="button" onClick={() => onSelectInvestor(null)} aria-label="Clear selected investor"><X size={13} /></button></div>}
    <div className="rc-chat-thread" ref={threadRef} role="log" aria-label="Conversation about your shortlist" aria-live="polite" aria-relevant="additions text" aria-busy={busy}>
      {messages.map(message => <div className={`rc-chat-message rc-chat-message-${message.role}${message.failed ? " rc-chat-message-failed" : ""}`} key={message.id}>
        <span className="rc-chat-role">{message.role === "user" ? "You" : <><Sparkles size={11} />RoundMe</>}</span>
        <div className="rc-chat-bubble"><p>{message.content}</p>
          {message.failed && <span className="rc-chat-not-sent">No answer received. Your question is ready to retry.</span>}
          {message.candidateIds.length > 0 && <div className="rc-chat-investors" aria-label="Investors referenced in this answer">{message.candidateIds.map(id => {
            const card = result.candidates.find(candidate => candidate.investor.id === id)!;
            return <button type="button" key={id} aria-pressed={id === selectedInvestorId} onClick={() => onSelectInvestor(id)} title={`Highlight ${card.investor.name} in the knowledge graph`}>{card.investor.name}<ArrowUpRight size={12} /></button>;
          })}</div>}
          <Citations sources={message.citations} />
          {message.origin && message.origin !== "welcome" && <div className="rc-chat-answer-meta"><span>{message.origin === "model" ? "Live model response · based on this run" : "From retrieved evidence"}</span><CopyReply content={message.content} /></div>}
        </div>
      </div>)}
      {busy && <div className="rc-chat-thinking" role="status"><LoaderCircle size={14} className="spinning" /><span>Generating a reply from your round evidence…</span></div>}
      {error && <div className="rc-chat-error" role="alert"><p>{error}</p>{failedQuestion && <button type="button" disabled={busy} onClick={() => void send(failedQuestion.message, failedQuestion.investorId)}><RotateCcw size={12} />Retry question</button>}</div>}
    </div>
    <div className="rc-chat-suggestions" aria-label="Suggested questions">{SUGGESTIONS.map((suggestion, index) => {
      const question = index === 0 && selected ? "Why this investor?" : suggestion;
      return <button type="button" key={suggestion} disabled={busy} onClick={() => void send(question)}>{question}</button>;
    })}</div>
    <form className="rc-chat-composer" onSubmit={submit}>
      <label className="rc-chat-input-label" htmlFor={inputId}>Ask about your investors</label>
      <div className="rc-chat-input-wrap"><textarea id={inputId} rows={2} maxLength={2000} value={input} disabled={busy} onChange={event => setInput(event.target.value)} onKeyDown={keyDown}
        placeholder={selected ? `Ask about ${selected.investor.name}…` : "Ask about fit, connections or your next introduction…"} aria-describedby={hintId} />
        <button className="rc-chat-send" type="submit" disabled={busy || !input.trim()} aria-label="Send question">{busy ? <LoaderCircle size={16} className="spinning" /> : <Send size={16} />}</button></div>
      <div className="rc-chat-composer-hint" id={hintId}><span>Enter to send · Shift + Enter for a new line</span><span>{input.length}/2000</span></div>
    </form>
  </section>;
}
