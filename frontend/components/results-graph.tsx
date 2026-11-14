"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Building2, ChevronDown, Crosshair, Download, Expand, GitBranch, Maximize2, Minimize2, Network, Plus, Search, UserRound, X, ZoomIn, ZoomOut } from "lucide-react";
import { buildResultGraph, type ResultGraphEdge, type ResultGraphNode } from "../lib/result-graph";
import { createForceGraphData, filterForceGraphData, forceGraphDetail, graphFilterCounts, graphZoomPercent, humanRelationshipType, rememberForceGraphPositions,
  type CanvasLink, type CanvasNode, type ForceGraphData, type ForceGraphFilters, type GraphPosition } from "../lib/force-graph-data";
import type { RunResult } from "../lib/types";
import type { GraphCanvasHandle } from "./force-graph-canvas";
import GraphEvidenceInspector, { graphKindName } from "./force-graph-inspector";
import styles from "./force-graph.module.css";

const GraphCanvas = dynamic(() => import("./force-graph-canvas"), { ssr: false,
  loading: () => <div className={styles.loading} role="status">Arranging your connections…</div>,
});
type Props = { result: RunResult; selectedInvestorId: string | null; onSelectInvestor: (id: string | null) => void };
type Selection = { kind: "node" | "edge"; id: string } | null;
type Menu = { x: number; y: number } | null;
const emptyFilters = (): ForceGraphFilters => ({ direction: "all", entityTypes: new Set(), relationshipTypes: new Set(), searchQuery: "" });

export default function ResultsGraph(props: Props) { return <GraphExplorer key={props.result.run_id} {...props} />; }

function GraphExplorer({ result, selectedInvestorId, onSelectInvestor }: Props) {
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const graph = useMemo(() => buildResultGraph(result, { expandedNodeIds: expandedIds }), [result, expandedIds]);
  const [filters, setFilters] = useState<ForceGraphFilters>(emptyFilters);
  const [selection, setSelection] = useState<Selection>(null);
  const [menu, setMenu] = useState<Menu>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [expandedView, setExpandedView] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [reducedMotion, setReducedMotion] = useState(false);
  const [settled, setSettled] = useState(false);
  const [relationQuery, setRelationQuery] = useState("");
  const [exportError, setExportError] = useState(false);
  const shell = useRef<HTMLElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const canvas = useRef<GraphCanvasHandle | null>(null);
  const positions = useRef(new Map<string, GraphPosition>());
  const previousData = useRef<ForceGraphData | null>(null);
  const expandingHub = useRef<string | null>(null);
  const lastInvestorId = useRef(selectedInvestorId);
  const pendingFocus = useRef<string | null>(null);
  const rootId = graph.nodes.find(node => node.kind === "founder")?.id;
  const seedIds = useMemo(() => new Set(graph.nodes.filter(node => node.kind === "founder" || node.kind === "lead" || expandedIds.includes(node.id)).map(node => node.id)), [graph, expandedIds]);
  const expandedSet = useMemo(() => new Set(expandedIds), [expandedIds]);

  const allData = useMemo(() => {
    if (previousData.current) rememberForceGraphPositions(previousData.current.nodes, positions.current);
    const next = createForceGraphData(graph, positions.current);
    const hub = expandingHub.current ? positions.current.get(expandingHub.current) : null;
    let added = 0;
    for (const node of next.nodes) if (!positions.current.has(node.id)) {
      if (hub) {
        const angle = added++ * Math.PI * (3 - Math.sqrt(5));
        node.x = hub.x + Math.cos(angle) * 12; node.y = hub.y + Math.sin(angle) * 12;
        node.vx = reducedMotion ? 0 : Math.cos(angle) * 1.8; node.vy = reducedMotion ? 0 : Math.sin(angle) * 1.8;
      } else { node.x = (node.x - graph.width / 2) * .17; node.y = (node.y - graph.height / 2) * .17; }
    }
    expandingHub.current = null;
    return next;
    // Motion preference changes rendering, not topology or saved positions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);
  const data = useMemo(() => {
    // The same objects survive filter toggles, preserving the live force positions.
    const filtered = filterForceGraphData(allData, filters, { rootId, seedIds });
    previousData.current = filtered; return filtered;
  }, [allData, filters, rootId, seedIds]);
  const counts = useMemo(() => graphFilterCounts(allData), [allData]);
  const hasEvidence = graph.edges.some(edge => edge.kind !== "founder_route");
  const selectedNode = selection?.kind === "node" ? graph.nodes.find(node => node.id === selection.id) : undefined;
  const selectedEdge = selection?.kind === "edge" ? graph.edges.find(edge => edge.id === selection.id) : undefined;
  const neighbors = useMemo(() => {
    const ids = new Set<string>();
    if (selection?.kind === "node") for (const edge of graph.edges) {
      if (edge.source === selection.id) ids.add(edge.target);
      if (edge.target === selection.id) ids.add(edge.source);
    }
    return ids;
  }, [graph, selection]);
  const savePositions = useCallback((nodes: CanvasNode[]) => rememberForceGraphPositions(nodes, positions.current), []);
  const engineSettled = useCallback(() => setSettled(true), []);
  const activeFilters = filters.direction !== "all" || filters.entityTypes.size > 0 || filters.relationshipTypes.size > 0 || !!filters.searchQuery.trim();
  const entities = data.nodes.filter(node => !seedIds.has(node.id));
  const labelForId = (id: string) => graph.nodes.find(node => node.id === id)?.label || "Entity";
  const edgeTitle = (edge: ResultGraphEdge) => `${labelForId(edge.source)} → ${labelForId(edge.target)} · ${edge.label}`;

  useEffect(() => {
    const element = viewport.current; if (!element) return;
    const observer = new ResizeObserver(entries => { const rect = entries[0]?.contentRect;
      if (rect && rect.width > 0 && rect.height > 0) setSize({ width: Math.floor(rect.width), height: Math.floor(rect.height) });
    });
    observer.observe(element); return () => observer.disconnect();
  }, [hasEvidence]);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches); update();
    query.addEventListener("change", update); return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => { setSettled(false); }, [data]);
  useEffect(() => {
    if (selection && !(selection.kind === "node" ? graph.nodes : graph.edges).some(item => item.id === selection.id)) {
      setSelection(null); setMenu(null); setDetailsOpen(false);
    }
  }, [graph, selection]);
  useEffect(() => {
    const changed = selectedInvestorId !== lastInvestorId.current;
    lastInvestorId.current = selectedInvestorId;
    if (selectedInvestorId) {
      const node = graph.nodes.find(item => item.card && item.calaId === selectedInvestorId);
      setSelection(node ? { kind: "node", id: node.id } : null);
      if (node && changed) {
        pendingFocus.current = node.id;
        setFilters(emptyFilters()); setMenu(null);
      }
    } else setSelection(previous => previous?.kind === "node" && graph.nodes.find(node => node.id === previous.id)?.card ? null : previous);
  }, [selectedInvestorId, graph]);
  useEffect(() => {
    const id = pendingFocus.current;
    if (id && data.nodes.some(node => node.id === id)) { canvas.current?.focus(id); pendingFocus.current = null; }
  }, [data]);
  useEffect(() => {
    if (!expandedView) return;
    const previousOverflow = document.body.style.overflow, previousFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = "hidden"; shell.current?.focus();
    return () => { document.body.style.overflow = previousOverflow; previousFocus?.focus(); };
  }, [expandedView]);
  useEffect(() => {
    if (!menu) return;
    const element = menuRef.current;
    element?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return () => { if (document.activeElement === document.body || element?.contains(document.activeElement)) shell.current?.focus({ preventScroll: true }); };
  }, [menu]);

  function clearSelection() { setSelection(null); setMenu(null); setDetailsOpen(false); onSelectInvestor(null); }
  function chooseNode(node: ResultGraphNode, openDetails = false) {
    setSelection({ kind: "node", id: node.id }); setMenu(null); setDetailsOpen(openDetails); onSelectInvestor(node.card ? node.calaId : null);
  }
  function chooseEdge(edge: ResultGraphEdge, openDetails = true) {
    setSelection({ kind: "edge", id: edge.id }); setMenu(null); setDetailsOpen(openDetails); onSelectInvestor(null);
  }
  function cursorMenu(event: MouseEvent): Menu {
    const rect = stage.current?.getBoundingClientRect(); if (!rect) return null;
    return { x: Math.max(8, Math.min(event.clientX - rect.left, rect.width - 240)), y: Math.max(8, Math.min(event.clientY - rect.top, rect.height - 195)) };
  }
  function clickNode(node: CanvasNode, event: MouseEvent) {
    if (selection?.kind === "node" && selection.id === node.id && menu) { clearSelection(); return; }
    chooseNode(node); setMenu(cursorMenu(event));
  }
  function clickLink(link: CanvasLink, event: MouseEvent) {
    const edge = graph.edges.find(item => item.id === link.id); if (!edge) return;
    chooseEdge(edge, false); setMenu(cursorMenu(event));
  }
  function toggleExpansion(node: ResultGraphNode) {
    savePositions(data.nodes); setMenu(null); setSettled(false);
    const already = expandedIds.includes(node.id); expandingHub.current = already ? null : node.id;
    setExpandedIds(previous => already ? previous.filter(id => id !== node.id) : [...previous, node.id]);
  }
  function toggleFilter(key: "entityTypes" | "relationshipTypes", value: string) {
    savePositions(data.nodes); setMenu(null);
    setFilters(previous => { const next = new Set<string>(previous[key]); if (next.has(value)) next.delete(value); else next.add(value);
      return { ...previous, [key]: next } as ForceGraphFilters; });
  }
  function focusNode(node: ResultGraphNode) { chooseNode(node); canvas.current?.focus(node.id); if (size.width < 550) setRailOpen(false); }
  function resetFilters() { savePositions(data.nodes); setFilters(emptyFilters()); setMenu(null); }
  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.stopPropagation(); if (menu) setMenu(null); else if (expandedView) setExpandedView(false); else clearSelection(); }
    if (event.key === "Tab" && (expandedView || menu)) {
      const boundary = menu ? menuRef.current : shell.current;
      const controls = boundary?.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, summary, a[href], [tabindex="0"]');
      const items = [...(controls || [])].filter(item => item.getClientRects().length > 0);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === boundary)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === boundary)) { event.preventDefault(); first.focus(); }
    }
    if (menu && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      const items = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || [])];
      const index = items.indexOf(document.activeElement as HTMLButtonElement); event.preventDefault();
      items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }
  }

  return <section ref={shell} tabIndex={-1} className={`${styles.shell}${expandedView ? ` ${styles.expanded}` : ""}`} aria-label="Founder and investor knowledge graph"
    role={expandedView ? "dialog" : undefined} aria-modal={expandedView || undefined} onKeyDown={onKeyDown}>
    <header className={styles.header}><div><span className="kg-kicker"><GitBranch size={13} />Knowledge graph</span><h2 className={styles.title}>Explore your connections</h2></div>
      <div className={styles.headerActions}><button type="button" className={styles.iconButton} aria-label={expandedView ? "Close expanded graph" : "Expand graph view"} title={expandedView ? "Close expanded graph" : "Expand graph view"} onClick={() => setExpandedView(!expandedView)}>{expandedView ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button></div></header>
    {!hasEvidence ? <div className={styles.empty}><Network size={35} /><h3>{result.state === "no_open_allocation" ? "No graph search was needed" : "No sourced relationships to display"}</h3><p>People, companies and connections appear when Cala returns identified funding events or sourced roles.</p></div> : <>
      <div className={styles.filterBar}>
        <label className={styles.search}><Search size={13} /><input aria-label="Search graph entities" placeholder="Find an entity…" value={filters.searchQuery} onChange={event => { savePositions(data.nodes); setFilters(previous => ({ ...previous, searchQuery: event.target.value })); setMenu(null); }} /></label>
        <select className={styles.direction} aria-label="Relationship direction" title="Direction relative to explored hubs" value={filters.direction} onChange={event => { savePositions(data.nodes); setFilters(previous => ({ ...previous, direction: event.target.value as ForceGraphFilters["direction"] })); }}>
          <option value="all">All directions</option><option value="outgoing">Outgoing from hubs</option><option value="incoming">Incoming to hubs</option></select>
        <details className={styles.filter}><summary>Entities{filters.entityTypes.size > 0 && <span className={styles.filterCount}>{filters.entityTypes.size}</span>}<ChevronDown size={12} /></summary><div className={styles.filterPopover}>
          {(["company", "person"] as const).map(type => <label key={type} className={styles.filterOption}><input type="checkbox" checked={filters.entityTypes.has(type)} onChange={() => toggleFilter("entityTypes", type)} />{type === "company" ? "Organizations" : "People"}<span>{counts.entityTypes[type]}</span></label>)}
          <small>Select categories to filter the graph.</small></div></details>
        <details className={styles.filter}><summary>Relations{filters.relationshipTypes.size > 0 && <span className={styles.filterCount}>{filters.relationshipTypes.size}</span>}<ChevronDown size={12} /></summary><div className={styles.filterPopover}>
          <label className={styles.search}><Search size={12} /><input aria-label="Search relationship types" placeholder="Find a relation…" value={relationQuery} onChange={event => setRelationQuery(event.target.value)} /></label>
          {Object.entries(counts.relationshipTypes).filter(([type]) => humanRelationshipType(type).toLowerCase().includes(relationQuery.toLowerCase())).map(([type, count]) => <label key={type} className={styles.filterOption}><input type="checkbox" checked={filters.relationshipTypes.has(type)} onChange={() => toggleFilter("relationshipTypes", type)} />{humanRelationshipType(type)}<span>{count}</span></label>)}</div></details>
        {activeFilters && <button type="button" className={styles.iconButton} aria-label="Clear graph filters" title="Clear graph filters" onClick={resetFilters}><X size={13} /></button>}
      </div>
      <div ref={stage} className={styles.stage} data-kg-detail={forceGraphDetail(zoom)} data-kg-zoom={zoom} data-kg-simulating={!settled && !reducedMotion} data-kg-reduced-motion={reducedMotion} data-kg-node-count={data.nodes.length} data-kg-edge-count={data.links.length}>
        <div ref={viewport} className={styles.viewport} role="group" aria-label="Interactive network of people and organizations" aria-describedby="graph-instructions">
          {size.width > 0 && size.height > 0 && <GraphCanvas data={data} width={size.width} height={size.height} apiRef={canvas} selectedId={selectedNode?.id || null} selectedEdgeId={selectedEdge?.id || null}
            neighborIds={neighbors} expandedIds={expandedSet} reducedMotion={reducedMotion} onSelectNode={clickNode} onSelectLink={clickLink}
            onBackground={() => { if (menu) setMenu(null); else clearSelection(); }} onZoom={setZoom} onPositions={savePositions} onSettled={engineSettled} />}
        </div>
        <aside className={styles.rail} aria-label="Graph explorer"><div className={styles.railHeader}><button className={styles.railToggle} type="button" aria-expanded={railOpen} aria-label="Toggle explored entities" onClick={() => setRailOpen(!railOpen)}><Network size={13} />Explored hubs <span>{seedIds.size}</span><ChevronDown size={12} /></button></div>
          {railOpen && <><div className={styles.seedList}>{graph.nodes.filter(node => seedIds.has(node.id)).map(node => <div className={styles.seedCard} key={node.id} data-graph-seed={node.id}>
            <span className={styles.seedIcon}>{node.kind === "person" ? <UserRound size={13} /> : <Building2 size={13} />}</span><span className={styles.seedText}><strong>{node.label}</strong><small>{graphKindName(node)} · {node.degree} links</small></span>
            <div className={styles.seedActions}><button type="button" className={styles.iconButton} aria-label={`Focus ${node.label}`} onClick={() => focusNode(node)}><Crosshair size={12} /></button>{expandedIds.includes(node.id) && <button type="button" className={styles.iconButton} aria-label={`Remove explored hub ${node.label}`} onClick={() => toggleExpansion(node)}><X size={12} /></button>}</div>
          </div>)}</div>{expandedIds.length > 0 && <button type="button" className={styles.menuAction} onClick={() => { savePositions(data.nodes); setExpandedIds([]); }}>Clear expansions</button>}
            <details className={styles.entityBrowser}><summary>Browse entities <span>{entities.length}</span></summary><div className={styles.entityList}>{entities.map(node => <button key={node.id} type="button" className={`${styles.entityButton}${selection?.id === node.id ? ` ${styles.activeButton}` : ""}`} aria-pressed={selection?.id === node.id} aria-label={`Inspect ${graphKindName(node)}: ${node.label}`} onClick={() => { chooseNode(node, true); canvas.current?.focus(node.id); }}>
              {node.kind === "person" ? <UserRound size={12} /> : <Building2 size={12} />}<span>{node.label}</span></button>)}</div></details></>}
        </aside>
        {!data.links.length && <div className={styles.noMatches}><Search size={22} /><strong>No matching connections</strong><span>Try fewer filters or a different entity name.</span><button className={styles.menuAction} onClick={resetFilters}>Clear filters</button></div>}
        {menu && <div ref={menuRef} role="menu" aria-label="Graph selection actions" className={styles.menu} style={{ left: menu.x, top: menu.y }}>
          <div className={styles.menuHeading}>{selectedNode ? <>{selectedNode.kind === "person" ? <UserRound size={14} /> : <Building2 size={14} />}<span><strong>{selectedNode.label}</strong><small>{graphKindName(selectedNode)} · {selectedNode.degree} connections</small></span></> : <span><strong>{selectedEdge && edgeTitle(selectedEdge)}</strong><small>{selectedEdge?.sources.length || 0} source records</small></span>}</div>
          <button type="button" role="menuitem" className={styles.menuAction} onClick={() => { setDetailsOpen(true); setMenu(null); }}><Search size={13} />View details & evidence</button>
          {selectedNode && <><button type="button" role="menuitem" className={styles.menuAction} disabled={expandedIds.includes(selectedNode.id)} onClick={() => toggleExpansion(selectedNode)}><Plus size={13} />{expandedIds.includes(selectedNode.id) ? "Expanded" : "Expand connections"}</button><button type="button" role="menuitem" className={styles.menuAction} onClick={() => { canvas.current?.focus(selectedNode.id); setMenu(null); }}><Crosshair size={13} />Focus entity</button></>}
          <button type="button" role="menuitem" className={styles.menuAction} onClick={() => setMenu(null)}><X size={13} />Close menu</button>
        </div>}
        <div className={styles.camera} aria-label="Graph camera controls"><button type="button" className={styles.iconButton} aria-label="Zoom graph in" title="Zoom in" disabled={zoom >= 20} onClick={() => canvas.current?.zoomBy(1.5)}><ZoomIn size={16} /></button><output className={styles.zoom} aria-label="Graph zoom">{graphZoomPercent(zoom)}%</output>
          <button type="button" className={styles.iconButton} aria-label="Zoom graph out" title="Zoom out" disabled={zoom <= .1} onClick={() => canvas.current?.zoomBy(1 / 1.5)}><ZoomOut size={16} /></button><button type="button" className={styles.iconButton} aria-label="Fit all graph entities" title="Fit graph" onClick={() => canvas.current?.fit()}><Expand size={15} /></button>
          <button type="button" className={styles.iconButton} aria-label="Export graph as PNG" title="Export PNG" onClick={() => { try { canvas.current?.exportPng(); setExportError(false); } catch { setExportError(true); } }}><Download size={15} /></button></div>
        <div className={styles.status} aria-live="polite">{data.nodes.length} entities · {data.links.length} connections{exportError ? " · Export unavailable" : ""}</div>
      </div>
      <div className={styles.legend}><span><i className={styles.legendDot} />Organization</span><span><i className={styles.legendSquare} />Person</span><span>Larger = more connections</span>
        {selection ? <button className={styles.menuAction} type="button" onClick={() => setDetailsOpen(!detailsOpen)}>{detailsOpen ? "Hide details" : "View selection details"}<ChevronDown size={12} /></button> : <span>Click to explore</span>}
      </div>
      {detailsOpen && (selectedNode || selectedEdge) && <div className={styles.inspector}><GraphEvidenceInspector result={result} node={selectedNode} edge={selectedEdge} nodes={graph.nodes} edges={graph.edges} onChooseEdge={chooseEdge} onExpand={toggleExpansion} expanded={!!selectedNode && expandedIds.includes(selectedNode.id)} onClose={() => setDetailsOpen(false)} /></div>}
    </>}
    <p id="graph-instructions" className={styles.caption}>{result.state === "partial" ? "Partial evidence · " : ""}Drag to explore. Zoom for labels. Dashed line = your selected lead; funding and role evidence stay on the connections.{graph.truncated ? " Expand hubs to explore more retrieved evidence." : ""}</p>
  </section>;
}
