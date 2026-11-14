"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type RefObject } from "react";
import { Building2, ChevronDown, ChevronUp, ExternalLink, GitBranch, Move, Network, RotateCcw, X, ZoomIn, ZoomOut } from "lucide-react";
import { buildResultGraph, graphFocus, graphSources, type ResultGraphEdge, type ResultGraphNode } from "../lib/result-graph";
import { createGraphPhysics, pinGraphPhysicsNode, releaseGraphPhysicsNode, settleGraphPhysics, stopGraphPhysics, syncGraphPhysics, tickGraphPhysics, type GraphPhysicsInput, type GraphPhysicsState } from "../lib/graph-physics";
import type { Role, RunResult, Source } from "../lib/types";
import EntityMetadata from "./entity-metadata";

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(word => word[0]).join("").toUpperCase();
const ROLE_NAMES: Record<Role["kind"], string> = {
  works_at: "Employment affiliation", member_of: "Organization membership", board: "Board affiliation",
  founded: "Founder", studied_at: "Education affiliation",
};
const KIND_NAMES: Record<ResultGraphNode["kind"], string> = {
  founder: "Your company", lead: "Selected lead", investor: "Shortlisted investor", company: "Company", person: "Person",
};

function shortLines(text: string, max = 22): string[] {
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

function rolePeriod(asOf: string, since?: string | null, until?: string | null, current = false): string {
  if (until) {
    const range = `${since ? dateText(since) : "Start date unknown"} – ${dateText(until)}`;
    if (until.slice(0, 10) < asOf.slice(0, 10)) return `${range} · ${current ? "Current status unconfirmed; reported dates conflict" : "Historical role"}`;
    return `${range} · ${current ? "Current, explicitly reported by Cala" : "Current status unconfirmed"}`;
  }
  if (current) return `${since ? `${dateText(since)} – ` : ""}Current, explicitly reported by Cala`;
  return `${since ? `Started ${dateText(since)}; end date unreported.` : "Role dates not supplied."} Current status unconfirmed.`;
}

function nodeKindName(node: ResultGraphNode): string {
  return node.card ? node.card.review_status === "flagged" ? "Investor requiring review" : "Shortlisted investor" : KIND_NAMES[node.kind];
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

type Point = { x: number; y: number };
type Selection = { kind: "node" | "edge"; id: string } | null;
type Detail = "dot" | "icon" | "card";
type Drag = {
  kind: "pan" | "node"; nodeId?: string; start: Point; origin: Point; moved: boolean;
  element: SVGSVGElement | SVGGElement; pointerId: number;
};

/** Motion sleeps once settled, when offscreen, and while the document is hidden. */
function useGraphMotion(input: GraphPhysicsInput, canvas: RefObject<SVGSVGElement | null>, enabled: boolean) {
  const engine = useRef<GraphPhysicsState | null>(null);
  const frame = useRef<number | null>(null);
  const visible = useRef(true);
  const reduced = useRef(false);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [simulating, setSimulating] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const publish = () => {
    if (engine.current) setPositions(Object.fromEntries([...engine.current.nodes].map(([id, node]) => [id, { x: node.x, y: node.y }])));
  };
  const pause = () => { if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; setSimulating(false); };
  const wake = () => {
    const state = engine.current;
    if (!state || !visible.current || document.hidden || frame.current !== null) return;
    if (reduced.current) { if (state.active) settleGraphPhysics(state); publish(); return; }
    if (!state.active) { publish(); return; }
    let last = performance.now(), published = last;
    setSimulating(true);
    const run = (now: number) => {
      frame.current = null;
      if (!visible.current || document.hidden || engine.current !== state) { setSimulating(false); return; }
      const moving = tickGraphPhysics(state, now - last); last = now;
      if (now - published >= 32 || !moving) { publish(); published = now; }
      if (moving) frame.current = requestAnimationFrame(run); else setSimulating(false);
    };
    frame.current = requestAnimationFrame(run);
  };
  const wakeRef = useRef(wake); wakeRef.current = wake;
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => {
      reduced.current = preference.matches; setReduceMotion(preference.matches);
      if (engine.current) {
        engine.current.reducedMotion = preference.matches;
        if (preference.matches) { pause(); if (engine.current.active) settleGraphPhysics(engine.current); publish(); }
        else wakeRef.current();
      }
    };
    update(); preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (engine.current) syncGraphPhysics(engine.current, { ...input, reducedMotion: reduced.current });
    else engine.current = createGraphPhysics({ ...input, reducedMotion: reduced.current });
    publish(); if (enabled) wakeRef.current();
  }, [input, enabled]);
  useEffect(() => {
    const element = canvas.current;
    if (!enabled || !element) return;
    const observer = new IntersectionObserver(entries => {
      visible.current = entries[0]?.isIntersecting ?? true;
      if (visible.current) wakeRef.current(); else pause();
    });
    observer.observe(element);
    const visibility = () => { if (document.hidden) pause(); else wakeRef.current(); };
    document.addEventListener("visibilitychange", visibility);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", visibility); };
  }, [enabled, canvas]);
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current); if (engine.current) stopGraphPhysics(engine.current); }, []);
  return {
    positions, simulating, reduceMotion,
    pin(id: string, x: number, y: number) { if (engine.current) { pinGraphPhysicsNode(engine.current, id, x, y); publish(); wakeRef.current(); } },
    release(id: string) { if (engine.current) { releaseGraphPhysicsNode(engine.current, id); wakeRef.current(); } },
    reset() { pause(); engine.current = createGraphPhysics({ ...input, reducedMotion: reduced.current }); publish(); wakeRef.current(); },
    position(id: string) { const node = engine.current?.nodes.get(id); return node ? { x: node.x, y: node.y } : null; },
  };
}

export default function ResultsGraph({ result, selectedInvestorId, onSelectInvestor }: {
  result: RunResult;
  selectedInvestorId: string | null;
  onSelectInvestor: (id: string | null) => void;
}) {
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const graph = useMemo(() => buildResultGraph(result, { expandedNodeIds: expandedIds }), [result, expandedIds]);
  const [selection, setSelection] = useState<Selection>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const drag = useRef<Drag | null>(null);
  const lastDrag = useRef(0);
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expandedHub = useRef<string | undefined>(undefined);
  const touches = useRef(new Map<number, Point>());
  const pinch = useRef<{ distance: number; zoom: number; world: Point } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const markerId = useId().replaceAll(":", "");
  const graphDescriptionId = `${markerId}-description`;
  const hasEvidence = graph.edges.some(edge => edge.kind !== "founder_route");
  const compact = canvasSize.width > 0 && canvasSize.width < 500;
  const baseScale = Math.min(canvasSize.width / graph.width, canvasSize.height / graph.height);
  const effectiveScale = baseScale > 0 ? baseScale * zoom : .6;
  const detail: Detail = effectiveScale < .34 ? "dot" : effectiveScale < 1.15 ? "icon" : "card";
  const readableZoom = compact && baseScale > 0 ? Math.min(6, Math.max(1.6, 12 / (18 * baseScale))) : 1;
  const physicsInput = useMemo<GraphPhysicsInput>(() => ({
    width: graph.width, height: graph.height, expandedHubId: expandedHub.current, expandedHubIds: graph.expandedNodeIds,
    nodes: graph.nodes.map(node => ({ id: node.id, x: node.x, y: node.y, radius: node.radius, hubId: node.hubId,
      collision: detail === "card" ? { width: 232 / Math.max(.08, effectiveScale), height: 100 / Math.max(.08, effectiveScale) }
        : detail === "dot" ? { width: (14 + node.radius * .16) / Math.max(.08, effectiveScale), height: (14 + node.radius * .16) / Math.max(.08, effectiveScale) }
          : { width: 220, height: node.radius * 2 + 66, offsetY: 26 },
    })),
    links: graph.edges.map(edge => ({ source: edge.source, target: edge.target })),
  }), [graph, detail, effectiveScale]);
  const motion = useGraphMotion(physicsInput, svg, hasEvidence);
  useEffect(() => { expandedHub.current = undefined; }, [physicsInput]);
  const points = useMemo(() => new Map(graph.nodes.map(node => [node.id, { ...node, ...motion.positions[node.id] }])), [graph, motion.positions]);
  const focus = useMemo(() => graphFocus(graph, selection?.id || null), [graph, selection]);
  const inspected = selection?.kind === "node" ? points.get(selection.id) : undefined;
  const inspectedEdge = selection?.kind === "edge" ? graph.edges.find(edge => edge.id === selection.id) : undefined;
  const incidentEdges = inspected ? graph.edges.filter(edge => edge.source === inspected.id || edge.target === inspected.id) : [];
  const view = useRef({ zoom, pan, width: graph.width, height: graph.height });
  view.current = { zoom, pan, width: graph.width, height: graph.height };
  const parallel = useMemo(() => {
    const groups = new Map<string, ResultGraphEdge[]>();
    graph.edges.forEach(edge => {
      const key = [edge.source, edge.target].sort().join("|");
      groups.set(key, [...(groups.get(key) || []), edge]);
    });
    const offsets = new Map<string, number>();
    groups.forEach(edges => edges.sort((a, b) => a.id.localeCompare(b.id)).forEach((edge, index) =>
      offsets.set(edge.id, (index - (edges.length - 1) / 2) * 24)));
    return offsets;
  }, [graph]);

  useEffect(() => { setSelection(null); setZoom(1); setPan({ x: 0, y: 0 }); setExpandedIds([]); expandedHub.current = undefined; }, [result.run_id]);
  useEffect(() => () => { if (clickTimer.current) clearTimeout(clickTimer.current); }, []);
  useEffect(() => {
    const canvas = svg.current;
    if (!canvas || !hasEvidence) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1);
      zoomAt(view.current.zoom * Math.exp(-delta * .0016), canvasPoint(event.clientX, event.clientY));
    };
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => canvas.removeEventListener("wheel", wheel);
  }, [hasEvidence]);
  useEffect(() => {
    const canvas = svg.current;
    if (!canvas) return;
    const observer = new ResizeObserver(entries => {
      const bounds = entries[0]?.contentRect;
      if (bounds) setCanvasSize({ width: bounds.width, height: bounds.height });
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [hasEvidence]);
  useEffect(() => {
    setZoom(readableZoom);
    const node = selection?.kind === "node" ? points.get(selection.id) : null;
    setPan(node && compact ? { x: (graph.width / 2 - node.x) * readableZoom, y: (graph.height / 2 - node.y) * readableZoom } : { x: 0, y: 0 });
    // A responsive transition changes the view, not the chosen entity or a user's later zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compact, result.run_id]);
  useEffect(() => {
    if (selectedInvestorId) {
      const node = graph.nodes.find(item => item.card && item.calaId === selectedInvestorId);
      setSelection(node ? { kind: "node", id: node.id } : null);
      if (node) {
        const point = motion.position(node.id) || points.get(node.id) || node;
        if (compact) centerPoint(point); else ensureVisible(point);
      }
    } else {
      setSelection(previous => previous?.kind === "node" && graph.nodes.find(node => node.id === previous.id)?.card ? null : previous);
    }
  }, [selectedInvestorId, graph]);

  function clearSelection() { setSelection(null); onSelectInvestor(null); }
  function canvasPoint(clientX: number, clientY: number): Point {
    const bounds = svg.current?.getBoundingClientRect();
    const current = view.current;
    if (!bounds) return { x: current.width / 2, y: current.height / 2 };
    const scale = Math.min(bounds.width / current.width, bounds.height / current.height) || 1;
    return { x: (clientX - bounds.left - (bounds.width - current.width * scale) / 2) / scale,
      y: (clientY - bounds.top - (bounds.height - current.height * scale) / 2) / scale };
  }
  function zoomAt(requested: number, anchor: Point) {
    const current = view.current, next = Math.min(6, Math.max(.25, requested));
    const ratio = next / current.zoom;
    const nextPan = { x: anchor.x - current.width / 2 - ratio * (anchor.x - current.width / 2 - current.pan.x),
      y: anchor.y - current.height / 2 - ratio * (anchor.y - current.height / 2 - current.pan.y) };
    view.current = { ...current, zoom: next, pan: nextPan }; setZoom(next); setPan(nextPan);
  }
  function centerPoint(point: Point, scale = zoom) { setPan({ x: (graph.width / 2 - point.x) * scale, y: (graph.height / 2 - point.y) * scale }); }
  function ensureVisible(point: Point) {
    const x = graph.width / 2 + pan.x + (point.x - graph.width / 2) * zoom;
    const y = graph.height / 2 + pan.y + (point.y - graph.height / 2) * zoom;
    if (x < 90 || x > graph.width - 90 || y < 90 || y > graph.height - 100) centerPoint(point);
  }
  function showAll() { clearSelection(); setZoom(1); setPan({ x: 0, y: 0 }); }
  function adjustZoom(delta: number) {
    const next = Math.min(6, Math.max(.25, zoom + delta));
    const point = selection?.kind === "node" ? motion.position(selection.id) || points.get(selection.id) : null;
    setZoom(next);
    if (point) centerPoint(point, next);
    else setPan(previous => ({ x: previous.x * next / zoom, y: previous.y * next / zoom }));
  }
  function expandNode(node: ResultGraphNode) {
    if (clickTimer.current) { clearTimeout(clickTimer.current); clickTimer.current = null; }
    setSelection({ kind: "node", id: node.id }); onSelectInvestor(node.card ? node.calaId : null);
    const expanded = expandedIds.includes(node.id);
    expandedHub.current = expanded ? undefined : node.id;
    setExpandedIds(previous => expanded ? previous.filter(id => id !== node.id) : [...previous, node.id]);
    if (!expanded) centerPoint(motion.position(node.id) || node);
  }
  function clickNode(event: MouseEvent<SVGGElement>, node: ResultGraphNode) {
    event.stopPropagation();
    if (Date.now() - lastDrag.current <= 300) return;
    if (clickTimer.current) clearTimeout(clickTimer.current);
    if (event.detail > 1) return;
    clickTimer.current = setTimeout(() => { chooseNode(node); clickTimer.current = null; }, 200);
  }
  function chooseNode(node: ResultGraphNode) {
    if (selection?.kind === "node" && selection.id === node.id) { clearSelection(); return; }
    setSelection({ kind: "node", id: node.id });
    onSelectInvestor(node.card ? node.calaId : null);
    if (compact) centerPoint(motion.position(node.id) || points.get(node.id) || node);
    else ensureVisible(motion.position(node.id) || points.get(node.id) || node);
  }
  function chooseEdge(edge: ResultGraphEdge) {
    if (selection?.kind === "edge" && selection.id === edge.id) { clearSelection(); return; }
    setSelection({ kind: "edge", id: edge.id }); onSelectInvestor(null);
    const from = points.get(edge.source), to = points.get(edge.target);
    if (from && to) {
      const midpoint = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
      if (compact) centerPoint(midpoint); else ensureVisible(midpoint);
    }
  }
  function keyChoose(event: KeyboardEvent<SVGGElement>, choose: () => void) {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); choose(); }
  }
  function beginTouch(event: PointerEvent<SVGSVGElement | SVGGElement>) {
    if (event.pointerType !== "touch") return false;
    touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (touches.current.size < 2) return false;
    const [a, b] = [...touches.current.values()];
    const center = canvasPoint((a.x + b.x) / 2, (a.y + b.y) / 2), current = view.current;
    pinch.current = { distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), zoom: current.zoom,
      world: { x: current.width / 2 + (center.x - current.width / 2 - current.pan.x) / current.zoom,
        y: current.height / 2 + (center.y - current.height / 2 - current.pan.y) / current.zoom } };
    const active = drag.current;
    if (active?.nodeId) motion.release(active.nodeId);
    if (active?.element.hasPointerCapture(active.pointerId)) active.element.releasePointerCapture(active.pointerId);
    drag.current = null; lastDrag.current = Date.now(); setDragging(false);
    touches.current.forEach((_, id) => svg.current?.setPointerCapture(id));
    event.stopPropagation(); return true;
  }
  function startPan(event: PointerEvent<SVGSVGElement>) {
    if (beginTouch(event)) return;
    if (event.button !== 0 || (event.target as Element).closest("[data-kg-node], [data-kg-edge]")) return;
    if (clickTimer.current) { clearTimeout(clickTimer.current); clickTimer.current = null; }
    drag.current = { kind: "pan", start: { x: event.clientX, y: event.clientY }, origin: pan, moved: false, element: event.currentTarget, pointerId: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function startNodeDrag(event: PointerEvent<SVGGElement>, node: ResultGraphNode) {
    if (beginTouch(event)) return;
    if (event.button !== 0) return;
    if (clickTimer.current) { clearTimeout(clickTimer.current); clickTimer.current = null; }
    event.stopPropagation();
    drag.current = { kind: "node", nodeId: node.id, start: { x: event.clientX, y: event.clientY }, origin: motion.position(node.id) || points.get(node.id) || node,
      moved: false, element: event.currentTarget, pointerId: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function movePointer(event: PointerEvent<SVGSVGElement>) {
    if (event.pointerType === "touch" && touches.current.has(event.pointerId)) touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pinch.current && touches.current.size >= 2) {
      const [a, b] = [...touches.current.values()], gesture = pinch.current;
      const next = Math.min(6, Math.max(.25, gesture.zoom * Math.hypot(a.x - b.x, a.y - b.y) / gesture.distance));
      const center = canvasPoint((a.x + b.x) / 2, (a.y + b.y) / 2), current = view.current;
      const nextPan = { x: center.x - current.width / 2 - next * (gesture.world.x - current.width / 2),
        y: center.y - current.height / 2 - next * (gesture.world.y - current.height / 2) };
      view.current = { ...current, zoom: next, pan: nextPan }; setZoom(next); setPan(nextPan); return;
    }
    const active = drag.current;
    if (!active || !svg.current) return;
    const bounds = svg.current.getBoundingClientRect();
    const scale = Math.min(bounds.width / graph.width, bounds.height / graph.height);
    if (!scale) return;
    const dx = event.clientX - active.start.x, dy = event.clientY - active.start.y;
    if (!active.moved && Math.hypot(dx, dy) <= 4) return;
    active.moved = true; setDragging(true);
    if (active.kind === "pan") setPan({ x: active.origin.x + dx / scale, y: active.origin.y + dy / scale });
    else if (active.nodeId) {
      const id = active.nodeId;
      motion.pin(id, Math.min(graph.width - 70, Math.max(70, active.origin.x + dx / scale / zoom)),
        Math.min(graph.height - 90, Math.max(65, active.origin.y + dy / scale / zoom)));
    }
  }
  function endPointer(event: PointerEvent<SVGSVGElement>) {
    touches.current.delete(event.pointerId);
    if (pinch.current && touches.current.size < 2) { pinch.current = null; lastDrag.current = Date.now(); }
    const active = drag.current;
    if (!active) return;
    if (active.moved) lastDrag.current = Date.now();
    if (active.nodeId && active.moved) motion.release(active.nodeId);
    if (active.element.hasPointerCapture(active.pointerId)) active.element.releasePointerCapture(active.pointerId);
    drag.current = null; setDragging(false);
  }
  function resetView() {
    if (clickTimer.current) { clearTimeout(clickTimer.current); clickTimer.current = null; }
    setZoom(readableZoom); motion.reset();
    const node = graph.nodes.find(item => item.id === selection?.id);
    if (compact && node) centerPoint(node, readableZoom); else setPan({ x: 0, y: 0 });
  }
  function edgePath(edge: ResultGraphEdge) {
    const from = points.get(edge.source), to = points.get(edge.target);
    if (!from || !to) return "";
    const dx = to.x - from.x, dy = to.y - from.y, distance = Math.hypot(dx, dy) || 1;
    const ux = dx / distance, uy = dy / distance;
    const inset = (node: ResultGraphNode) => detail === "card"
      ? Math.min(110 / Math.max(.08, effectiveScale) / Math.max(Math.abs(ux), .01), 44 / Math.max(.08, effectiveScale) / Math.max(Math.abs(uy), .01))
      : node.kind === "person" ? visibleRadius(node) / Math.max(Math.abs(ux), Math.abs(uy), .01) : visibleRadius(node);
    const start = { x: from.x + ux * (inset(from) + 2), y: from.y + uy * (inset(from) + 2) };
    const end = { x: to.x - ux * (inset(to) + 5), y: to.y - uy * (inset(to) + 5) };
    const offset = parallel.get(edge.id) || 0;
    return `M ${start.x} ${start.y} Q ${(start.x + end.x) / 2 - uy * offset} ${(start.y + end.y) / 2 + ux * offset} ${end.x} ${end.y}`;
  }
  function visibleRadius(node: ResultGraphNode) { return detail === "dot" ? (3 + node.radius * .08) / Math.max(.08, effectiveScale) : node.radius; }
  function edgeLabel(edge: ResultGraphEdge) {
    const from = points.get(edge.source)?.label || "Unknown entity", to = points.get(edge.target)?.label || "Unknown entity";
    return `${from} → ${to} · ${edge.label}${edge.funding ? ` · ${dateText(edge.funding.event_date, edge.funding.event_date_precision)}` : ""}`;
  }
  const selectedLabel = inspected?.label || (inspectedEdge ? edgeLabel(inspectedEdge) : null);
  const renderConnection = (edge: ResultGraphEdge) => <li key={edge.id}><button type="button" className="kg-connection-button" onClick={() => chooseEdge(edge)}>
    <span>{points.get(edge.source)?.label} → {points.get(edge.target)?.label}<small>{edge.label}{edge.funding ? ` · ${dateText(edge.funding.event_date, edge.funding.event_date_precision)}` : ""}</small></span></button></li>;

  return <section className="kg-shell" aria-label="Founder and investor knowledge graph">
    <div className="kg-toolbar"><div><span className="kg-kicker"><GitBranch size={13} />Knowledge graph</span><h2>Your connections, explained</h2></div>
      <div className="kg-controls"><button type="button" aria-label="Zoom graph out" onClick={() => adjustZoom(-.35)} disabled={!hasEvidence || zoom <= .25}><ZoomOut size={15} /></button>
        <button type="button" aria-label="Zoom graph in" onClick={() => adjustZoom(.35)} disabled={!hasEvidence || zoom >= 6}><ZoomIn size={15} /></button>
        <button type="button" aria-label="Reset graph view" disabled={!hasEvidence} onClick={resetView}><RotateCcw size={14} /></button></div></div>
    {!hasEvidence ? <div className="kg-empty"><Network size={40} strokeWidth={1.2} /><h3>{result.state === "no_open_allocation" ? "No graph search was needed" : "No sourced relationships to display"}</h3>
      <p>{result.state === "no_open_allocation" ? "Your firm commitments already meet the round target." : "People, organizations and their connections appear when Cala returns identified funding events or sourced roles."}</p></div>
      : <>
        <div className="kg-selection"><span>{selectedLabel ? `Showing ${selectedLabel}` : `${graph.investorCount} investors · ${graph.peopleCount} people · ${graph.eventCount} funding events`}</span>
          {selection ? <button type="button" onClick={showAll}>Show all connections</button> : <span className="kg-drag-hint"><Move size={12} />Drag nodes or background</span>}</div>
        {compact && <div className="kg-entity-picker"><select aria-label="Find a person or organization" value={selection?.kind === "node" ? selection.id : ""}
          onChange={event => { const node = points.get(event.target.value); if (node) { if (zoom < readableZoom) setZoom(readableZoom); if (selection?.kind !== "node" || selection.id !== node.id) chooseNode(node); centerPoint(motion.position(node.id) || node, Math.max(zoom, readableZoom)); } else showAll(); }}>
          <option value="">Find a person or organization…</option>{graph.nodes.map(node => <option key={node.id} value={node.id}>{node.label}{node.kind === "person" ? " · Person" : ""}</option>)}</select></div>}
        <div className={`kg-canvas-wrap${dragging ? " kg-is-dragging" : ""}`} data-kg-detail={detail} data-kg-simulating={motion.simulating}>
          <svg ref={svg} className={`kg-canvas${compact ? " kg-canvas-compact" : ""}`} data-kg-detail={detail} data-kg-simulating={motion.simulating} data-kg-zoom={zoom} data-kg-reduced-motion={motion.reduceMotion}
            viewBox={`0 0 ${graph.width} ${graph.height}`} aria-label="Interactive network of people, organizations, funding and role evidence" aria-describedby={graphDescriptionId} role="group"
            onPointerDown={startPan} onPointerMove={movePointer} onPointerUp={endPointer} onPointerCancel={endPointer}
            onClick={event => { if (event.target === event.currentTarget && Date.now() - lastDrag.current > 300) clearSelection(); }}
            onKeyDown={event => { if (event.key === "Escape") clearSelection(); }} style={{ touchAction: "none" }}>
            <desc id={graphDescriptionId}>Squares are people; circles are organizations. Larger nodes have more unique neighbors. Select a node to inspect it, or double-click to expand its sourced neighborhood. Dragged nodes repel neighbors and spring back when released. Zoom with the controls, wheel or pinch to reveal more detail.</desc>
            <defs><marker id={`kg-arrow-${markerId}`} viewBox="0 0 8 8" refX="6" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M 1 1 L 7 4 L 1 7" fill="none" stroke="currentColor" strokeWidth="1.2" /></marker></defs>
            <g transform={`translate(${graph.width / 2 + pan.x} ${graph.height / 2 + pan.y}) scale(${zoom}) translate(${-graph.width / 2} ${-graph.height / 2})`}>
              {graph.edges.map(edge => <g key={edge.id} data-kg-edge={edge.id} role="button" tabIndex={0} aria-pressed={selection?.kind === "edge" && selection.id === edge.id}
                aria-label={`Relationship: ${edgeLabel(edge)}`} className={`kg-edge-control${selection?.kind === "edge" && selection.id === edge.id ? " kg-edge-selected" : ""}`}
                onClick={event => { event.stopPropagation(); chooseEdge(edge); }} onKeyDown={event => keyChoose(event, () => chooseEdge(edge))}
                onFocus={event => { const from = points.get(edge.source), to = points.get(edge.target); if (event.currentTarget.matches(":focus-visible") && from && to) ensureVisible({ x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 }); }}>
                <title>{edgeLabel(edge)}</title>
                <path className="kg-edge-hit" d={edgePath(edge)} fill="none" vectorEffect="non-scaling-stroke" />
                <path d={edgePath(edge)} className={`kg-edge kg-edge-${edge.kind}${edge.confirmed ? " kg-edge-confirmed" : ""}${focus && !focus.edges.has(edge.id) ? " kg-dimmed" : ""}${focus?.edges.has(edge.id) ? " kg-highlighted" : ""}`}
                  fill="none" vectorEffect="non-scaling-stroke" markerEnd={edge.kind === "investment" || edge.kind === "role" ? `url(#kg-arrow-${markerId})` : undefined} />
              </g>)}
              {graph.nodes.map(original => {
                const node = points.get(original.id)!;
                const lines = shortLines(node.label);
                const chosen = selection?.kind === "node" && selection.id === node.id;
                const radius = visibleRadius(node), expanded = graph.expandedNodeIds.includes(node.id);
                const cardLines = shortLines(node.label, 25);
                return <g key={node.id} data-kg-node={node.id} data-kg-degree={node.degree} data-kg-radius={node.radius} data-kg-x={node.x} data-kg-y={node.y}
                  data-kg-expanded={expanded} data-kg-hub={node.hubId || ""} data-kg-detail={detail}
                  role="button" tabIndex={0} aria-pressed={chosen} aria-label={`${nodeKindName(node)}: ${node.label}`}
                  className={`kg-node kg-node-${node.kind}${expanded ? " kg-node-hub" : ""}${node.card ? " kg-node-candidate" : ""}${chosen ? " kg-node-selected" : ""}${focus?.nodes.has(node.id) ? " kg-node-neighbor" : ""}${focus && !focus.nodes.has(node.id) ? " kg-dimmed" : ""}`}
                  transform={`translate(${node.x} ${node.y})`} onPointerDown={event => startNodeDrag(event, node)}
                  onClick={event => clickNode(event, node)} onDoubleClick={event => { event.stopPropagation(); if (Date.now() - lastDrag.current > 300) expandNode(node); }} onKeyDown={event => keyChoose(event, () => chooseNode(node))}
                  onFocus={event => { if (event.currentTarget.matches(":focus-visible")) ensureVisible(node); }}>
                  <title>{node.label} · {node.degree} unique {node.degree === 1 ? "connection" : "connections"}</title>
                  {detail === "card" ? <g className="kg-card-content" transform={`scale(${1 / Math.max(.08, effectiveScale)})`}>
                    <rect className="kg-node-halo" x="-115" y="-49" width="230" height="98" rx="12" />
                    <rect className="kg-node-card" x="-110" y="-44" width="220" height="88" rx="9" />
                    {node.kind === "person" ? <rect className="kg-node-shape" x="-94" y="-22" width="32" height="32" rx="4" /> : <circle className="kg-node-shape" cx="-78" cy="-6" r="16" />}
                    <text className="kg-card-initials kg-node-initials" x="-78" y="-6" textAnchor="middle" dominantBaseline="central">{initials(node.label)}</text>
                    {cardLines.map((line, index) => <text className="kg-card-title" x="-49" y={-19 + index * 15} key={index}>{line}</text>)}
                    <text className="kg-card-meta" x="-94" y="24">{node.card ? `${node.card.fit_score} ${result.scoring_version.startsWith("rules-") ? "rules met" : "priority"}` : node.roles[0] || KIND_NAMES[node.kind]}</text>
                    <text className="kg-card-meta" x="100" y="38" textAnchor="end">{expanded ? "Neighborhood hub · " : ""}{node.degree} {node.degree === 1 ? "connection" : "connections"}</text>
                  </g> : <>
                    <circle className="kg-node-hit" r={Math.max(radius + 6, 18 / Math.max(.08, effectiveScale))} />
                    {node.kind === "person" ? <><rect className="kg-node-halo" x={-radius - 6} y={-radius - 6} width={(radius + 6) * 2} height={(radius + 6) * 2} rx="7" />
                      <rect className="kg-node-shape" x={-radius} y={-radius} width={radius * 2} height={radius * 2} rx={detail === "dot" ? 1 : 4} /></>
                      : <><circle className="kg-node-halo" r={radius + 6} /><circle className="kg-node-shape" r={radius} /></>}
                    {detail === "icon" && <text className="kg-node-initials" textAnchor="middle" dominantBaseline="central">{initials(node.label)}</text>}
                    {detail === "icon" && lines.map((line, index) => <text className="kg-node-label" x="0" y={radius + 23 + index * 19} textAnchor="middle" key={index}>{line}</text>)}
                  </>}
                </g>;
              })}
            </g>
          </svg>
        </div>
        <div className="kg-legend"><span><i className="kg-legend-organization" />Organization</span><span><i className="kg-legend-person" />Person</span>
          <span><i className="kg-legend-line" />Funding</span><span><i className="kg-legend-line kg-legend-trust" />Role / overlap</span>
          <span><i className="kg-legend-line kg-legend-dashed" />Founder-selected lead</span><span>Larger = more connections</span></div>
        <div className="kg-inspector" aria-live="polite">{inspectedEdge ? <>
          <div className="kg-inspector-heading"><div><span className="kg-kicker">{inspectedEdge.funding ? "Funding event evidence" : inspectedEdge.role ? "Sourced affiliation" : inspectedEdge.kind === "founder_route" ? "Founder-selected route" : "Professional relationship evidence"}</span><h3>{edgeLabel(inspectedEdge)}</h3></div>
            <button type="button" className="kg-inspector-close" aria-label="Clear graph selection" onClick={clearSelection}><X size={15} /></button></div>
          {inspectedEdge.funding ? <>
            <p><strong>{points.get(inspectedEdge.source)?.label}</strong> participated in funding for <strong>{inspectedEdge.funding.company.name}</strong>. The line represents this identified event.</p>
            <dl className="kg-evidence-facts"><div><dt>Stage</dt><dd>{inspectedEdge.funding.stage?.replaceAll("_", " ") || "Unknown"}</dd></div>
              <div><dt>Event date</dt><dd>{dateText(inspectedEdge.funding.event_date, inspectedEdge.funding.event_date_precision)}</dd></div>
              <div><dt>Recipient</dt><dd>{inspectedEdge.funding.company.name}</dd></div>
              {inspectedEdge.funding.total_amount_eur && <div><dt>Round total</dt><dd>{new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(inspectedEdge.funding.total_amount_eur))} · actual investor checks unknown</dd></div>}</dl>
            <p>Historical participation does not confirm an introduction, a personal relationship or interest in your current round.</p>
            <small className="kg-entity-id">Cala funding event: {inspectedEdge.funding.id}</small>
          </> : inspectedEdge.role ? <><p>{ROLE_NAMES[inspectedEdge.role.kind]} · {inspectedEdge.role.person.name} → {inspectedEdge.role.org.name}</p>
            <p>{rolePeriod(result.confirmed_at, inspectedEdge.role.valid_since, inspectedEdge.role.valid_until, inspectedEdge.role.current)}</p>
            <p>An organizational role does not confirm a personal relationship or introduction access.</p></>
            : inspectedEdge.hop ? <><p>{inspectedEdge.hop.org ? `Organizational context: ${inspectedEdge.hop.org.name}. ` : ""}{rolePeriod(result.confirmed_at, inspectedEdge.hop.since, inspectedEdge.hop.until)}</p>
              <p>{inspectedEdge.confirmed ? "This path includes a connection confirmed by the founder. Public evidence is listed below." : "Public professional evidence; personal relationship and introduction access are unconfirmed."}</p></>
              : <p>The founder selected {result.lead?.name} as their lead. Current-round commitment: {result.profile.round.lead_status.replaceAll("_", " ")}. This route is founder supplied.</p>}
          {inspectedEdge.kind !== "founder_route" && <Evidence sources={inspectedEdge.sources} />}
        </> : inspected ? <>
          <div className="kg-inspector-heading"><div><span className="kg-kicker">{nodeKindName(inspected)}{inspected.card && inspected.kind === "person" ? " · Person" : ""}</span><h3>{inspected.label}</h3></div>
            <button type="button" className="kg-inspector-close" aria-label="Clear graph selection" onClick={clearSelection}><X size={15} /></button></div>
          <p className="kg-node-context">{inspected.degree} unique {inspected.degree === 1 ? "connection" : "connections"}{inspected.roles.length ? ` · ${inspected.roles.join(" · ")}` : ""}</p>
          <div className="kg-expansion-actions"><button type="button" onClick={() => expandNode(inspected)} aria-label={`${graph.expandedNodeIds.includes(inspected.id) ? "Collapse" : "Expand"} connections for ${inspected.label}`}>
            {graph.expandedNodeIds.includes(inspected.id) ? <ChevronUp size={14} /> : <ChevronDown size={14} />}{graph.expandedNodeIds.includes(inspected.id) ? "Collapse connections" : "Expand connections"}</button>
            <span>{graph.expandedNodeIds.includes(inspected.id) ? "Neighborhood hub" : inspected.availableNeighborCount ? `${inspected.availableNeighborCount} more sourced neighbors` : "Explore this neighborhood"}</span></div>
          {inspected.kind === "founder" ? <p>{result.profile.startup.sector}{result.profile.startup.country ? ` · ${result.profile.startup.country}` : ""}. The company identity and selected lead were confirmed by the founder; funding and role evidence is shown on individual connections.</p>
            : inspected.kind === "person" ? <p>{inspected.entity?.description || "Cala identifies this person in retrieved role or funding records."} Role dates and their source links are available on the connected lines. A shared organization does not establish that people know each other.</p>
              : inspected.card ? <p>{result.scoring_version.startsWith("rules-") ? `${inspected.card.fit_score} rules met.` : `Introduction priority ${inspected.card.fit_score}/100.`} Select this investor in the shortlist to discuss them with the round assistant or open their full details.</p>
                : <p>{inspected.entity?.description || (inspected.kind === "lead" ? "The founder selected this organization as their lead. Historical funding is shown on separately sourced connections." : "An organization identified in the retrieved funding or professional-role evidence.")}</p>}
          {incidentEdges.length > 0 && <div className="kg-node-connections"><h4>Connections and evidence</h4><ul>{incidentEdges.slice(0, 5).map(renderConnection)}</ul>{incidentEdges.length > 5 && <details><summary>{incidentEdges.length - 5} more connections</summary><ul>{incidentEdges.slice(5).map(renderConnection)}</ul></details>}</div>}
          {inspected.calaId && <small className="kg-entity-id">Cala ID: {inspected.calaId}</small>}
          {inspected.entity && <EntityMetadata entity={inspected.entity} />}
          {inspected.sources.length > 0 && <Evidence sources={inspected.sources} />}
        </> : <div className="kg-inspector-empty"><Building2 size={17} /><div><h3>Explore the evidence</h3><p>Select a person or organization to focus their neighbors. Select a line for its funding event or role evidence. Select again to clear; drag nodes to arrange them.</p></div></div>}</div>
      </>}
    <p className="kg-caption">{result.state === "partial" ? "Partial graph · " : ""}Lead network: {result.lookback_months}-month lookback. Founder-history connections may include older records. Lines retain their own sources; public affiliations do not establish personal access.{graph.truncated ? " Some retrieved entities or events are outside this bounded view." : ""}</p>
  </section>;
}
