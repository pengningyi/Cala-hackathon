import type { ResultGraph, ResultGraphEdge, ResultGraphNode } from "./result-graph";

export type CanvasEntityType = "company" | "person";
export type GraphDirection = "all" | "outgoing" | "incoming";
export type GraphDetail = "marker" | "icon" | "short" | "full";
export interface GraphPosition { x: number; y: number; vx: number; vy: number }
export interface CanvasNode extends ResultGraphNode {
  name: string;
  entityType: CanvasEntityType;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
  isLoading?: boolean;
}
export interface CanvasLink extends Omit<ResultGraphEdge, "source" | "target"> {
  source: string | CanvasNode;
  target: string | CanvasNode;
  relationshipType: string;
  curvature: number;
}
export interface ForceGraphData { nodes: CanvasNode[]; links: CanvasLink[] }
export interface ForceGraphFilters {
  direction: GraphDirection;
  entityTypes: ReadonlySet<CanvasEntityType>;
  relationshipTypes: ReadonlySet<string>;
  searchQuery: string;
}

/** force-graph replaces endpoint IDs with mutable node objects after initialization. */
export const forceGraphId = (endpoint: string | { id: string }): string =>
  typeof endpoint === "string" ? endpoint : endpoint.id;

const pairKey = (link: CanvasLink): string =>
  JSON.stringify([forceGraphId(link.source), forceGraphId(link.target)].sort());

/** Each evidence-bearing edge survives; parallel funding events must not be merged. */
export function assignParallelCurvature(links: readonly CanvasLink[]): CanvasLink[] {
  const copies = links.map(link => ({ ...link }));
  const groups = new Map<string, CanvasLink[]>();
  for (const link of copies) {
    const key = pairKey(link);
    groups.set(key, [...(groups.get(key) || []), link]);
  }
  for (const group of groups.values()) {
    group.sort((a, b) => a.id.localeCompare(b.id));
    const selfLoop = forceGraphId(group[0].source) === forceGraphId(group[0].target);
    group.forEach((link, index) => {
      if (selfLoop) link.curvature = group.length === 1 ? 1 : .5 + index * .5 / (group.length - 1);
      else if (group.length === 1) link.curvature = 0;
      else {
        // Reversing endpoints reverses the geometric normal. Normalize that sign,
        // so opposite-direction relationships occupy separate physical curves too.
        const canonical = -.5 + index / (group.length - 1);
        link.curvature = forceGraphId(link.source) <= forceGraphId(link.target) ? canonical : -canonical;
      }
    });
  }
  return copies;
}

/** Clone the canvas-owned records: its physics must never mutate the run result. */
export function createForceGraphData(graph: ResultGraph, positions?: ReadonlyMap<string, GraphPosition>): ForceGraphData {
  const nodes = graph.nodes.map((node): CanvasNode => {
    const position = positions?.get(node.id);
    return {
      ...node,
      roles: [...node.roles],
      sources: [...node.sources],
      name: node.label,
      entityType: node.kind === "person" ? "person" : "company",
      ...(position && Number.isFinite(position.x) && Number.isFinite(position.y) ? {
        x: position.x, y: position.y,
        vx: Number.isFinite(position.vx) ? position.vx : 0,
        vy: Number.isFinite(position.vy) ? position.vy : 0,
      } : {}),
    };
  });
  const links = graph.edges.map((edge): CanvasLink => ({
    ...edge, source: edge.source, target: edge.target, sources: [...edge.sources],
    relationshipType: edge.role?.kind || edge.kind, curvature: 0,
  }));
  return { nodes, links: assignParallelCurvature(links) };
}

/** Retain hidden nodes too, so toggling a filter restores their last settled place. */
export function rememberForceGraphPositions(nodes: readonly CanvasNode[], positions: Map<string, GraphPosition>): void {
  for (const node of nodes) if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
    positions.set(node.id, { x: node.x, y: node.y,
      vx: Number.isFinite(node.vx) ? node.vx! : 0, vy: Number.isFinite(node.vy) ? node.vy! : 0 });
  }
}

export function graphFilterCounts(data: ForceGraphData): {
  entityTypes: Record<CanvasEntityType, number>; relationshipTypes: Record<string, number>;
} {
  const entityTypes: Record<CanvasEntityType, number> = { company: 0, person: 0 };
  const relationshipTypes: Record<string, number> = {};
  data.nodes.forEach(node => { entityTypes[node.entityType]++; });
  data.links.forEach(link => { relationshipTypes[link.relationshipType] = (relationshipTypes[link.relationshipType] || 0) + 1; });
  return { entityTypes, relationshipTypes };
}

/** Filters intersect. Anchor preservation never invents an edge to a removed node. */
export function filterForceGraphData(
  data: ForceGraphData,
  filters: ForceGraphFilters,
  options: { rootId?: string; seedIds?: ReadonlySet<string> | readonly string[] } = {},
): ForceGraphData {
  const seeds = new Set(options.seedIds || (options.rootId ? [options.rootId] : []));
  const query = filters.searchQuery.trim().toLocaleLowerCase();
  let links = data.links.filter(link => {
    if (filters.relationshipTypes.size && !filters.relationshipTypes.has(link.relationshipType)) return false;
    if (filters.direction === "outgoing" && !seeds.has(forceGraphId(link.source))) return false;
    if (filters.direction === "incoming" && !seeds.has(forceGraphId(link.target))) return false;
    return true;
  });
  const incident = new Set(links.flatMap(link => [forceGraphId(link.source), forceGraphId(link.target)]));
  const restrictToRelationships = filters.direction !== "all" || filters.relationshipTypes.size > 0;
  const nodes = data.nodes.filter(node => node.id === options.rootId || (
    (!filters.entityTypes.size || filters.entityTypes.has(node.entityType))
    && (!query || node.name.toLocaleLowerCase().includes(query))
    && (!restrictToRelationships || incident.has(node.id))
  ));
  const visible = new Set(nodes.map(node => node.id));
  links = links.filter(link => visible.has(forceGraphId(link.source)) && visible.has(forceGraphId(link.target)));
  return { nodes, links };
}

export const forceGraphDetail = (scale: number): GraphDetail =>
  scale >= 7 ? "full" : scale >= 3.5 ? "short" : scale >= 1.5 ? "icon" : "marker";

export const graphZoomPercent = (scale: number): number => {
  const value = Number.isFinite(scale) ? Math.max(.1, Math.min(20, scale)) : .1;
  return Math.round((Math.log(value) - Math.log(.1)) / (Math.log(20) - Math.log(.1)) * 100);
};

const RELATIONSHIP_NAMES: Record<string, string> = {
  founder_route: "Your selected lead", investment: "Investment", founded: "Founded",
  works_at: "Works at", member_of: "Member of", board: "Board affiliation",
  studied_at: "Studied at", role: "Affiliation", trust: "Sourced connection",
};
export const humanRelationshipType = (type: string): string => RELATIONSHIP_NAMES[type]
  || type.toLowerCase().replaceAll("_", " ").replace(/^\w/, character => character.toUpperCase());
