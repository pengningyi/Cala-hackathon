"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { MessageCircle, Radio } from "lucide-react";
import type { RunResult } from "../lib/types";
import ResultsChat from "./results-chat";
import type { ReportTurn } from "../lib/run-report";

interface Props {
  result: RunResult;
  selectedInvestorId: string | null;
  onSelectInvestor: (id: string | null) => void;
  trace: ReactNode;
  onConversationChange?: (turns: ReportTurn[]) => void;
  onEvidenceRevision?: (revision: number) => void;
}

type Tab = "chat" | "activity";

/** Live result conversation and the original search trace share one panel. */
export default function AgentInsights({ result, selectedInvestorId, onSelectInvestor, trace, onConversationChange, onEvidenceRevision }: Props) {
  const [tab, setTab] = useState<Tab>("chat");
  const id = useId();
  const chatTab = useRef<HTMLButtonElement>(null);
  const activityTab = useRef<HTMLButtonElement>(null);

  useEffect(() => { setTab("chat"); }, [result.run_id]);
  function switchTab(next: Tab) {
    setTab(next);
    (next === "chat" ? chatTab : activityTab).current?.focus();
  }
  function keyTabs(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const focused = event.currentTarget === chatTab.current ? "chat" : "activity";
    switchTab(event.key === "Home" ? "chat" : event.key === "End" ? "activity" : focused === "chat" ? "activity" : "chat");
  }

  return <section className="agent-insights" aria-label="Round assistant and agent activity">
    <div className="agent-insight-heading"><span className="agent-insight-icon"><MessageCircle size={19} /></span><div>
      <h2>Your round assistant</h2><p>Ask about your round, investors and next introduction</p></div>
      <span className="agent-insight-live"><span />Live chat</span></div>
    <div className="agent-insight-tabs" role="tablist" aria-label="Round assistant views">
      <button ref={chatTab} id={`${id}-chat-tab`} type="button" role="tab" aria-controls={`${id}-chat`}
        aria-selected={tab === "chat"} tabIndex={tab === "chat" ? 0 : -1} onKeyDown={keyTabs}
        onClick={() => setTab("chat")}><MessageCircle size={13} />Chat</button>
      <button ref={activityTab} id={`${id}-activity-tab`} type="button" role="tab" aria-controls={`${id}-activity`}
        aria-selected={tab === "activity"} tabIndex={tab === "activity" ? 0 : -1} onKeyDown={keyTabs}
        onClick={() => setTab("activity")}><Radio size={13} />Agent activity</button>
    </div>
    <div id={`${id}-chat`} role="tabpanel" aria-labelledby={`${id}-chat-tab`}
      className="agent-insight-panel agent-insight-chat" hidden={tab !== "chat"}>
      <ResultsChat key={result.run_id} result={result} selectedInvestorId={selectedInvestorId} onSelectInvestor={onSelectInvestor} embedded active={tab === "chat"} onConversationChange={onConversationChange} onEvidenceRevision={onEvidenceRevision} />
    </div>
    <div id={`${id}-activity`} role="tabpanel" aria-labelledby={`${id}-activity-tab`} tabIndex={0}
      className="agent-insight-panel agent-insight-activity" hidden={tab !== "activity"}>{trace}</div>
  </section>;
}
