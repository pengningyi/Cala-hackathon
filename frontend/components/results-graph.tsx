"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { ArrowUpRight, Building2, ExternalLink, GitBranch, Move, Network, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { buildResultGraph, graphFocus, graphSources, type ResultGraphEdge, type ResultGraphNode } from "../lib/result-graph";
import type { RunResult, Source } from "../lib/types";

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(word => word[0]).join("").toUpperCase();

function shortLines(text: string, max = 21): string[] {
  if (text.length <= max) return [text];
  const words = text.split(/\s+/);
  let first = "";
  while (words.length && `${first} ${words[0]}`.trim().length <= max) first = `${first} ${words.shift()}`.trim();
  if (!first) return [text.slice(0, max - 1) + "…"];
  const rest = words.join(" ");
  return [first, rest.length > max ? rest.slice(0, max - 1) + "…" : rest];
}

function dateText(value: string | null | undefined, precision?: string | null): string {
  if (!value) return "Date unknown";
  const parsed = new Date(value.includes("T") ? value : `${value}T12:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return "Date unknown";
  const options: Intl.DateTimeFormatOptions = precision === "year" ? { year: "numeric" }
    : precision === "month" ? { month: "short", year: "numeric" }
      : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" }).format(parsed);
}

function Evidence({ sources }: { sources: Source[] }) {
  const unique = graphSources(sources);
  const sourceItem = (source: Source, index: number) => <li key={`${source.url}-${index}`}>
    <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label || "Cala source"}<ExternalLink size={11} /></a>
    <span>{source.published_at ? `Published ${dateText(source.published_at)}` : "Publication date unknown"} · Retrieved {dateText(source.retrieved_at)}</span>
  </li>;
  if (!unique.length) return <p className="kg-evidence-empty">No public source link was returned for this detail.</p>;
  return <div className="kg-source-list"><ul>{unique.slice(0, 3).map(sourceItem)}</ul>{unique.length > 3 && <details>
    <summary>{unique.length - 3} more source links</summary><ul>{unique.slice(3).map(sourceItem)}</ul>
  </details>}</div>;
}

const kindName: Record<ResultGraphNode["kind"], string> = {
  founder: "Your company", lead: "Selected lead", investor: "Shortlisted investor", round: "Identified funding round", company: "Portfolio company",
};

export default function ResultsGraph({ result, selectedInvestorId, onSelectInvestor }: {
  result: RunResult;
  selectedInvestorId: string | null;
  onSelectInvestor: (id: string | null) => void;
}) {
  const graph = useMemo(() => buildResultGraph(result), [result]);
  const focus = useMemo(() => graphFocus(graph, selectedInvestorId), [graph, selectedInvestorId]);
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; panX: number; panY: number; moved: boolean } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const markerId = useId().replaceAll(":", "");
  useEffect(() => { setInspectedId(null); setZoom(1); setPan({ x: 0, y: 0 }); }, [result.run_id]);
  useEffect(() => {
    setInspectedId(selectedInvestorId ? `investor:${selectedInvestorId}` : null);
  }, [selectedInvestorId]);
  const inspected = graph.nodes.find(node => node.id === inspectedId);
  const selected = result.candidates.find(card => card.investor.id === selectedInvestorId);
  const points = useMemo(() => new Map(graph.nodes.map(node => [node.id, node])), [graph]);

  function choose(node: ResultGraphNode) {
    setInspectedId(node.id);
    if (node.kind === "investor") onSelectInvestor(node.calaId);
  }
  function keyChoose(event: KeyboardEvent<SVGGElement>, node: ResultGraphNode) {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(node); }
  }
  function startPan(event: PointerEvent<SVGSVGElement>) {
    if (event.button !== 0 || (event.target as Element).closest("[data-kg-node]")) return;
    drag.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function movePan(event: PointerEvent<SVGSVGElement>) {
    if (!drag.current || !svg.current) return;
    const bounds = svg.current.getBoundingClientRect();
    const scale = Math.min(bounds.width / graph.width, bounds.height / graph.height);
    if (!scale) return;
    const dx = event.clientX - drag.current.x, dy = event.clientY - drag.current.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) { drag.current.moved = true; setDragging(true); }
    setPan({ x: drag.current.panX + dx / scale, y: drag.current.panY + dy / scale });
  }
  function endPan(event: PointerEvent<SVGSVGElement>) {
    if (!drag.current) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null; setDragging(false);
  }
  function path(edge: ResultGraphEdge) {
    const from = points.get(edge.source), to = points.get(edge.target);
    if (!from || !to) return "";
    if (edge.kind === "founder_route") return `M ${from.x} ${from.y + 25} C ${from.x} ${from.y + 100}, ${to.x} ${to.y - 100}, ${to.x} ${to.y - 30}`;
    const mid = (from.x + to.x) / 2;
    return `M ${from.x} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${to.x} ${to.y}`;
  }

  return <section className="kg-shell" aria-label="Founder and investor knowledge graph">
    <div className="kg-toolbar"><div><span className="kg-kicker"><GitBranch size={13} />Knowledge graph</span><h2>Your connections, explained</h2></div>
      <div className="kg-controls"><button type="button" aria-label="Zoom graph out" onClick={() => setZoom(value => Math.max(.65, value - .2))} disabled={!graph.eventCount || zoom <= .65}><ZoomOut size={15} /></button>
        <button type="button" aria-label="Zoom graph in" onClick={() => setZoom(value => Math.min(2.4, value + .2))} disabled={!graph.eventCount || zoom >= 2.4}><ZoomIn size={15} /></button>
        <button type="button" aria-label="Reset graph view" disabled={!graph.eventCount} onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}><RotateCcw size={14} /></button></div></div>
    {!graph.eventCount ? <div className="kg-empty"><Network size={40} strokeWidth={1.2} /><h3>{result.state === "no_open_allocation" ? "No graph search was needed" : "No sourced shared rounds to display"}</h3>
      <p>{result.state === "no_open_allocation" ? "Your firm commitments already meet the round target." : "An investor connection appears here only when Cala identifies participation in the same funding event."}</p></div>
      : <>
        <div className="kg-selection"><span>{selected ? `Highlighting ${selected.investor.name}` : `${graph.investorCount} investors · ${graph.eventCount} identified ${graph.eventCount === 1 ? "round" : "rounds"}`}</span>
          {selected ? <button type="button" onClick={() => { onSelectInvestor(null); setInspectedId(null); }}>Show all connections</button> : <span><Move size={11} />Drag to explore</span>}</div>
        <div className={`kg-canvas-wrap${dragging ? " kg-is-dragging" : ""}`}>
          <svg ref={svg} className="kg-canvas" viewBox={`0 0 ${graph.width} ${graph.height}`} aria-label="Interactive graph of the founder-provided lead and Cala funding-event relationships" role="group"
            onPointerDown={startPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan} style={{ touchAction: "none" }}>
            <defs><marker id={`kg-arrow-${markerId}`} viewBox="0 0 8 8" refX="6" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M 1 1 L 7 4 L 1 7" fill="none" stroke="currentColor" strokeWidth="1.2" /></marker></defs>
            <g transform={`translate(${graph.width / 2 + pan.x} ${graph.height / 2 + pan.y}) scale(${zoom}) translate(${-graph.width / 2} ${-graph.height / 2})`}>
              <text className="kg-column-label" x="95" y="26" textAnchor="middle">YOUR ROUTE</text><text className="kg-column-label" x="300" y="26" textAnchor="middle">SHARED ROUNDS</text>
              <text className="kg-column-label" x="466" y="26" textAnchor="middle">COMPANIES</text><text className="kg-column-label" x="660" y="26" textAnchor="middle">CO-INVESTORS</text>
              {graph.edges.map(edge => <path key={edge.id} d={path(edge)} className={`kg-edge kg-edge-${edge.kind}${focus && !focus.edges.has(edge.id) ? " kg-dimmed" : ""}${focus?.edges.has(edge.id) ? " kg-highlighted" : ""}`}
                fill="none" markerEnd={edge.kind === "issuer" ? `url(#kg-arrow-${markerId})` : undefined}><title>{edge.kind === "founder_route" ? "Founder-supplied lead commitment; not a Cala relationship" : edge.kind === "issuer" ? "Cala identifies the recipient company of this funding event" : "Cala identifies investor participation in this exact funding event"}</title></path>)}
              <text className="kg-route-label" x="105" y="191">Founder supplied</text><text className="kg-route-subtitle" x="105" y="207">{result.profile.round.lead_status === "conditional" ? "Provisional lead" : result.profile.round.lead_status.replaceAll("_", " ")}</text>
              {graph.nodes.map(node => {
                const lines = shortLines(node.label, node.kind === "company" ? 19 : 23);
                const radius = node.kind === "lead" ? 29 : node.kind === "founder" ? 24 : node.kind === "round" ? 10 : node.kind === "company" ? 11 : 21;
                const chosen = inspectedId === node.id || node.kind === "investor" && node.calaId === selectedInvestorId;
                return <g key={node.id} data-kg-node={node.id} role="button" tabIndex={0} aria-pressed={chosen}
                  aria-label={`${kindName[node.kind]}: ${node.label}${node.funding ? `, ${dateText(node.funding.event_date, node.funding.event_date_precision)}` : ""}`}
                  className={`kg-node kg-node-${node.kind}${chosen ? " kg-node-selected" : ""}${focus && !focus.nodes.has(node.id) ? " kg-dimmed" : ""}`}
                  transform={`translate(${node.x} ${node.y})`} onClick={event => { event.stopPropagation(); choose(node); }} onKeyDown={event => keyChoose(event, node)}>
                  <title>{node.label}</title><circle className="kg-node-halo" r={radius + 6} /><circle className="kg-node-shape" r={radius} />
                  {node.kind === "round" ? <path className="kg-node-symbol" d="M-3-3h6v6h-6z" /> : node.kind === "company" ? <path className="kg-node-symbol" d="M-3 4V-4h6v8M-1-2h2M-1 0h2M-1 2h2" />
                    : <text className="kg-node-initials" textAnchor="middle" dominantBaseline="central">{initials(node.label)}</text>}
                  {node.kind === "round" ? graph.eventCount <= 6 && <>
                    <text className="kg-node-label" x="0" y="27" textAnchor="middle">{lines[0]}</text><text className="kg-node-meta" x="0" y="43" textAnchor="middle">{dateText(node.funding?.event_date, node.funding?.event_date_precision)}</text></>
                    : node.kind === "company" ? lines.slice(0, 2).map((line, index) => <text className="kg-node-label kg-company-label" x="19" y={lines.length > 1 ? index * 15 - 3 : 4} key={index}>{line}</text>)
                      : <>{lines.slice(0, 2).map((line, index) => <text className="kg-node-label" x="0" y={radius + 23 + index * 16} textAnchor="middle" key={index}>{line}</text>)}
                        <text className="kg-node-meta" x="0" y={radius + 23 + lines.length * 16} textAnchor="middle">{node.kind === "founder" ? "Founder profile" : node.kind === "lead" ? "Selected lead" : `Priority ${result.candidates.findIndex(card => card.investor.id === node.calaId) + 1}`}</text></>}
                </g>;
              })}
            </g>
          </svg>
        </div>
        <div className="kg-legend"><span><i className="kg-legend-line" />Cala event evidence</span><span><i className="kg-legend-line kg-legend-dashed" />Founder-supplied lead</span><span><i className="kg-legend-event" />Funding event</span></div>
        <div className="kg-inspector" aria-live="polite">{inspected ? <>
          <div className="kg-inspector-heading"><div><span className="kg-kicker">{kindName[inspected.kind]}</span><h3>{inspected.label}</h3></div><ArrowUpRight size={16} /></div>
          {inspected.kind === "founder" ? <><p>{result.profile.founder_name ? `${result.profile.founder_name} · ` : ""}{result.profile.startup.sector} · {result.profile.startup.country}. Startup details and the current lead commitment are founder-supplied.</p>
            {inspected.calaId && <p>Matched to Cala company {inspected.entity?.name || inspected.calaId} by the founder.</p>}</>
            : inspected.kind === "round" && inspected.funding ? <><p>{dateText(inspected.funding.event_date, inspected.funding.event_date_precision)} · {inspected.funding.stage?.replaceAll("_", " ") || "Stage unknown"} · Recipient: {inspected.funding.company.name}.</p><p>Lines show participation in this exact event. A shared round does not establish a personal relationship or current interest.</p></>
              : inspected.kind === "investor" && inspected.card ? <><p>{inspected.card.shared_round_count} shared {inspected.card.shared_round_count === 1 ? "round" : "rounds"} across {inspected.card.shared_company_count} {inspected.card.shared_company_count === 1 ? "company" : "companies"}. Introduction priority {inspected.card.fit_score}/100; profile completeness {Math.round(inspected.card.profile_completeness * 100)}%.</p><p>Inspect this investor in the shortlist or ask the assistant about the retrieved evidence.</p></>
                : <p>{inspected.entity?.description || (inspected.kind === "lead" ? "The founder selected this Cala entity as their current lead. Historical participation is shown separately through funding events." : "Cala identifies this company as the recipient of the connected funding event.")}</p>}
          {inspected.calaId && <small className="kg-entity-id">Cala ID: {inspected.calaId}</small>}
          {inspected.sources.length > 0 && <Evidence sources={inspected.sources} />}
        </> : <div className="kg-inspector-empty"><Building2 size={17} /><div><h3>Explore the evidence</h3><p>Select an investor, company or funding event to inspect its details and source dates. Use the shortlist to highlight a path.</p></div></div>}</div>
      </>}
    <p className="kg-caption">{result.state === "partial" ? "Partial graph · " : ""}{result.lookback_months}-month shared-investment lookback. Only retrieved, identified funding events are shown. The dashed founder route is separate from public Cala facts.</p>
  </section>;
}
