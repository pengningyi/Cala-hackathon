"use client";

import { useEffect, useImperativeHandle, useRef, useState, type MutableRefObject } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import { forceGraphId, type CanvasLink, type CanvasNode, type ForceGraphData } from "../lib/force-graph-data";
import { nodeWorldRadius, paintGraphGrid, paintGraphNode, paintGraphNodeHit } from "../lib/force-graph-paint";

// force-graph renders string tooltips as HTML; entity text is always escaped.
const tooltipText = (value: string) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

export interface GraphCanvasHandle {
  zoomBy: (factor: number) => void;
  fit: () => void;
  focus: (id: string) => void;
  exportPng: () => void;
}

export default function GraphCanvas({ data, width, height, apiRef, selectedId, neighborIds, expandedIds, selectedEdgeId,
  reducedMotion, onSelectNode, onSelectLink, onBackground, onZoom, onPositions, onSettled }: {
  data: ForceGraphData; width: number; height: number; apiRef: MutableRefObject<GraphCanvasHandle | null>;
  selectedId: string | null; neighborIds: ReadonlySet<string>; expandedIds: ReadonlySet<string>; selectedEdgeId: string | null;
  reducedMotion: boolean; onSelectNode: (node: CanvasNode, event: MouseEvent) => void;
  onSelectLink: (link: CanvasLink, event: MouseEvent) => void; onBackground: () => void;
  onZoom: (zoom: number) => void; onPositions: (nodes: CanvasNode[]) => void; onSettled: () => void;
}) {
  const engine = useRef<ForceGraphMethods<CanvasNode, CanvasLink> | undefined>(undefined);
  const wrapper = useRef<HTMLDivElement>(null);
  const firstFit = useRef(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const current = useRef({ data, reducedMotion, onPositions, onSettled });
  current.current = { data, reducedMotion, onPositions, onSettled };
  const paintState = { selectedId, neighborIds, expandedIds, hoveredId };
  const duration = reducedMotion ? 0 : 300;

  function fit() { firstFit.current = true; engine.current?.zoomToFit(duration, width < 450 ? 40 : 65); }
  useImperativeHandle(apiRef, () => ({
    zoomBy(factor) { const fg = engine.current; if (fg) { firstFit.current = true; fg.zoom(Math.min(20, Math.max(.1, fg.zoom() * factor)), duration); } },
    fit,
    focus(id) {
      const node = current.current.data.nodes.find(item => item.id === id), fg = engine.current;
      if (!node || !fg) return;
      firstFit.current = true;
      fg.centerAt(node.x, node.y, duration); fg.zoom(8, duration);
    },
    exportPng() {
      const source = wrapper.current?.querySelector("canvas");
      if (!source) return;
      const canvas = document.createElement("canvas"); canvas.width = source.width; canvas.height = source.height;
      const ctx = canvas.getContext("2d"); if (!ctx) return;
      ctx.fillStyle = "#fcfbfe"; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.drawImage(source, 0, 0);
      const link = document.createElement("a"); link.download = "roundcloser-connections.png"; link.href = canvas.toDataURL("image/png"); link.click();
    },
  }));

  useEffect(() => {
    const fg = engine.current; if (!fg) return;
    fg.d3Force("link")?.distance(62).strength(.18);
    fg.d3Force("charge")?.strength(-65).distanceMax(1000);
    fg.d3ReheatSimulation();
    return () => { onPositions(data.nodes); };
    // Filter changes reheat while reusing cached positions; they never reset the camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => {
    const element = wrapper.current; if (!element) return;
    let visible = true;
    const update = () => {
      if (document.hidden || !visible) engine.current?.pauseAnimation(); else engine.current?.resumeAnimation();
    };
    const observer = new IntersectionObserver(entries => { visible = entries[0]?.isIntersecting ?? true; update(); });
    observer.observe(element); document.addEventListener("visibilitychange", update);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", update); engine.current?.pauseAnimation(); };
  }, []);

  const highlighted = (link: CanvasLink) => link.id === selectedEdgeId || !!selectedId && (forceGraphId(link.source) === selectedId || forceGraphId(link.target) === selectedId);
  const linkColor = (link: CanvasLink) => highlighted(link) ? "#8560ab" : link.kind === "founder_route" ? "#a08aaf" : "#c8c2d2";
  return <div ref={wrapper} style={{ width, height }}>
    <ForceGraph2D<CanvasNode, CanvasLink> ref={engine} graphData={data} width={width} height={height}
      backgroundColor="#fcfbfe" minZoom={.1} maxZoom={20} nodeRelSize={1} nodeVal={node => nodeWorldRadius(node) ** 2}
      nodeLabel={node => tooltipText(`${node.label} · ${node.degree} connections`)}
      linkLabel={link => tooltipText(`${typeof link.source === "string" ? link.source : link.source.label} → ${typeof link.target === "string" ? link.target : link.target.label} · ${link.label}`)}
      nodeCanvasObject={(node, ctx, scale) => paintGraphNode(node, ctx, scale, paintState)}
      nodePointerAreaPaint={(node, color, ctx, scale) => paintGraphNodeHit(node, color, ctx, scale, paintState)}
      onRenderFramePre={paintGraphGrid}
      linkCurvature="curvature" linkColor={linkColor} linkWidth={link => highlighted(link) ? 1.5 : .7}
      linkLineDash={link => link.kind === "founder_route" ? [3, 3] : null}
      linkDirectionalArrowLength={link => link.kind === "founder_route" ? 0 : 2.5} linkDirectionalArrowRelPos={1}
      linkDirectionalArrowColor={linkColor} linkDirectionalParticles={link => !reducedMotion && highlighted(link) && link.kind !== "founder_route" ? 2 : 0}
      linkDirectionalParticleSpeed={.003} linkDirectionalParticleWidth={1.5} linkDirectionalParticleColor={() => "#8f6eb0"}
      d3VelocityDecay={.4} cooldownTicks={reducedMotion ? 0 : 80} warmupTicks={reducedMotion ? 80 : 0}
      onEngineStop={() => {
        onPositions(data.nodes); onSettled();
        if (!firstFit.current) { firstFit.current = true; fit(); }
      }}
      onNodeDragEnd={node => { delete node.fx; delete node.fy; engine.current?.d3ReheatSimulation(); onPositions(data.nodes); }}
      onNodeHover={node => setHoveredId(node?.id ?? null)} onNodeClick={onSelectNode}
      onNodeRightClick={(node, event) => { event.preventDefault(); onSelectNode(node, event); }}
      onLinkClick={onSelectLink} onLinkRightClick={(link, event) => { event.preventDefault(); onSelectLink(link, event); }}
      onBackgroundClick={onBackground} onZoom={transform => onZoom(transform.k)} linkHoverPrecision={7} />
  </div>;
}
