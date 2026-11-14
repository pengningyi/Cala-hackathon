import { expect, test } from "@playwright/test";
import {
  assignParallelCurvature, createForceGraphData, filterForceGraphData, forceGraphDetail, forceGraphId,
  graphFilterCounts, graphZoomPercent, humanRelationshipType, rememberForceGraphPositions,
  type ForceGraphFilters, type GraphPosition,
} from "../lib/force-graph-data";
import type { ResultGraph, ResultGraphEdge, ResultGraphNode } from "../lib/result-graph";

const evidence = { url: "https://synthetic.example.test/relationship", retrieved_at: "2026-11-14T00:00:00Z",
  published_at: null, label: "Synthetic evidence" };
const node = (id: string, kind: ResultGraphNode["kind"] = "company"): ResultGraphNode => ({
  id, kind, label: id === "p" ? "Synthetic Person" : `Company ${id}`, x: 10, y: 20, calaId: id,
  sources: [evidence], roles: [], degree: 1, radius: 12, availableNeighborCount: 0, totalNeighborCount: 1,
});
const edge = (id: string, source: string, target: string, kind: ResultGraphEdge["kind"] = "investment"): ResultGraphEdge => ({
  id, source, target, kind, label: `Evidence event ${id}`, eventId: kind === "investment" ? id : null,
  sources: [evidence],
});
const graph = (): ResultGraph => ({
  width: 400, height: 300, nodes: [node("root", "founder"), node("a", "investor"), node("b"), node("p", "person")],
  edges: [edge("one", "a", "root"), edge("two", "root", "b", "founder_route"), edge("three", "p", "a", "role")],
  eventCount: 1, investorCount: 1, peopleCount: 1, omittedNodeCount: 0, omittedEventCount: 0,
  truncated: false, expandedNodeIds: [],
});
const filters = (overrides: Partial<ForceGraphFilters> = {}): ForceGraphFilters => ({
  direction: "all", entityTypes: new Set(), relationshipTypes: new Set(), searchQuery: "", ...overrides,
});

test("canvas copies preserve separate funding-event evidence and cannot mutate graph positions/endpoints", () => {
  const original = graph();
  original.edges.push(edge("follow-on", "a", "root"));
  const cache = new Map<string, GraphPosition>([["a", { x: 73, y: 92, vx: 1, vy: 2 }]]);
  const canvas = createForceGraphData(original, cache);
  expect(canvas.links).toHaveLength(4);
  expect(canvas.nodes.find(item => item.id === "a")).toMatchObject({ x: 73, y: 92, vx: 1, vy: 2 });
  expect(canvas.nodes.find(item => item.id === "p")?.entityType).toBe("person");
  expect(canvas.nodes.find(item => item.id === "a")?.entityType).toBe("company");
  canvas.nodes[0].x = 999;
  canvas.links[0].source = canvas.nodes[1];
  canvas.links[0].sources.pop();
  expect(original.nodes[0].x).toBe(10);
  expect(original.edges[0].source).toBe("a");
  expect(original.edges[0].sources).toEqual([evidence]);
  expect(canvas.links.find(item => item.id === "follow-on")?.eventId).toBe("follow-on");
});

test("parallel edges receive stable distinct physical curves including reverse directions", () => {
  const input = graph();
  input.edges = [edge("c", "b", "a"), edge("a", "a", "b"), edge("b", "a", "b")];
  const first = createForceGraphData(input).links;
  const reverse = createForceGraphData({ ...input, edges: [...input.edges].reverse() }).links;
  const byId = (links: typeof first) => Object.fromEntries(links.map(link => [link.id, link.curvature]));
  expect(byId(first)).toEqual(byId(reverse));
  const physicalCurves = first.map(link => link.curvature * (forceGraphId(link.source) <= forceGraphId(link.target) ? 1 : -1));
  expect(new Set(physicalCurves).size).toBe(3);
  expect(input.edges.every(item => !("curvature" in item))).toBe(true);
});

test("self loops fan separately and a lone ordinary edge stays straight", () => {
  const input = graph();
  input.edges = [edge("loop1", "a", "a"), edge("loop2", "a", "a"), edge("single", "a", "b")];
  const links = createForceGraphData(input).links;
  expect(links.find(link => link.id === "loop1")?.curvature).toBe(.5);
  expect(links.find(link => link.id === "loop2")?.curvature).toBe(1);
  expect(links.find(link => link.id === "single")?.curvature).toBe(0);
  expect(assignParallelCurvature(links)).not.toBe(links);
});

test("entity, relationship and search filters intersect instead of admitting unrelated entities", () => {
  const data = createForceGraphData(graph());
  const result = filterForceGraphData(data, filters({ entityTypes: new Set(["person"]),
    relationshipTypes: new Set(["investment"]), searchQuery: "synthetic" }));
  expect(result.nodes).toEqual([]);
  expect(result.links).toEqual([]);
  const person = filterForceGraphData(data, filters({ entityTypes: new Set(["person"]), searchQuery: " SYNTHETIC " }));
  expect(person.nodes.map(item => item.id)).toEqual(["p"]);
  expect(person.links).toEqual([]);
});

test("preserving an anchor under filters does not preserve links to hidden endpoints", () => {
  const data = createForceGraphData(graph());
  const result = filterForceGraphData(data, filters({ entityTypes: new Set(["person"]), searchQuery: "no match" }), { rootId: "root" });
  expect(result.nodes.map(item => item.id)).toEqual(["root"]);
  expect(result.links).toEqual([]);
});

test("direction uses explored seeds and remains valid after the library replaces endpoint IDs", () => {
  const data = createForceGraphData(graph());
  data.links[0].source = data.nodes.find(item => item.id === "a")!;
  data.links[0].target = data.nodes.find(item => item.id === "root")!;
  const incoming = filterForceGraphData(data, filters({ direction: "incoming" }), { seedIds: ["root"] });
  const outgoing = filterForceGraphData(data, filters({ direction: "outgoing" }), { seedIds: new Set(["root"]) });
  expect(incoming.links.map(item => item.id)).toEqual(["one"]);
  expect(outgoing.links.map(item => item.id)).toEqual(["two"]);
  // A relation between two explored seeds is incoming to one and outgoing from the other.
  const bothIncoming = filterForceGraphData(data, filters({ direction: "incoming" }), { seedIds: ["root", "a"] });
  const bothOutgoing = filterForceGraphData(data, filters({ direction: "outgoing" }), { seedIds: ["root", "a"] });
  expect(bothIncoming.links.some(item => item.id === "one")).toBe(true);
  expect(bothOutgoing.links.some(item => item.id === "one")).toBe(true);
});

test("filter counts come from complete graph and relationships retain event labels", () => {
  const data = createForceGraphData(graph());
  const counts = graphFilterCounts(data);
  filterForceGraphData(data, filters({ entityTypes: new Set(["person"]) }));
  expect(counts).toEqual({ entityTypes: { company: 3, person: 1 },
    relationshipTypes: { investment: 1, founder_route: 1, role: 1 } });
  expect(data.links[0].label).toBe("Evidence event one");
  expect(humanRelationshipType("founder_route")).toBe("Your selected lead");
  expect(humanRelationshipType("NEW_RELATIONSHIP")).toBe("New relationship");
});

test("position memory preserves hidden nodes and ignores invalid simulation coordinates", () => {
  const data = createForceGraphData(graph());
  const positions = new Map<string, GraphPosition>();
  data.nodes[0].x = 105; data.nodes[0].y = -38; data.nodes[0].vx = Number.NaN;
  rememberForceGraphPositions(data.nodes, positions);
  const hiddenBefore = positions.get("p");
  const visible = filterForceGraphData(data, filters({ entityTypes: new Set(["company"]) }));
  visible.nodes[0].x = 150;
  rememberForceGraphPositions(visible.nodes, positions);
  expect(positions.get("p")).toBe(hiddenBefore);
  expect(positions.get("root")).toEqual({ x: 150, y: -38, vx: 0, vy: 0 });
  visible.nodes[0].x = Number.NaN;
  rememberForceGraphPositions(visible.nodes, positions);
  expect(positions.get("root")?.x).toBe(150);
  const restored = createForceGraphData(graph(), positions);
  expect(restored.nodes.find(item => item.id === "p")).toMatchObject({ ...hiddenBefore! });
});

test("LOD thresholds and log-normalized zoom percentages stay bounded", () => {
  expect([.1, 1.49, 1.5, 3.49, 3.5, 6.99, 7, 20].map(forceGraphDetail))
    .toEqual(["marker", "marker", "icon", "icon", "short", "short", "full", "full"]);
  expect(graphZoomPercent(.1)).toBe(0);
  expect(graphZoomPercent(20)).toBe(100);
  expect(graphZoomPercent(100)).toBe(100);
  expect(graphZoomPercent(-1)).toBe(0);
  expect(graphZoomPercent(Number.NaN)).toBe(0);
  expect(graphZoomPercent(Math.sqrt(.1 * 20))).toBe(50);
});
