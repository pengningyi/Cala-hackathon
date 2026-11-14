import { expect, test } from "@playwright/test";
import {
  createGraphPhysics, pinGraphPhysicsNode, releaseGraphPhysicsNode, settleGraphPhysics,
  stopGraphPhysics, syncGraphPhysics, tickGraphPhysics,
  type GraphPhysicsInput, type GraphPhysicsState, type PhysicsNodeInput,
} from "../lib/graph-physics";

const node = (id: string, x: number, y: number, radius = 18): PhysicsNodeInput => ({ id, x, y, radius });
const input = (nodes: PhysicsNodeInput[], extra: Partial<GraphPhysicsInput> = {}): GraphPhysicsInput =>
  ({ nodes, links: [], width: 1000, height: 800, ...extra });
function run(state: GraphPhysicsState, frames = 450) {
  for (let i = 0; i < frames && state.active; i++) tickGraphPhysics(state, 1000 / 60);
}
const point = (state: GraphPhysicsState, id: string) => ({ x: state.nodes.get(id)!.x, y: state.nodes.get(id)!.y });
const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

test("a dragged node stays pinned only during dragging and springs back to its saved home", () => {
  const state = createGraphPhysics(input([node("a", 300, 300)]));
  pinGraphPhysicsNode(state, "a", 530, 440);
  run(state, 20);
  expect(point(state, "a")).toEqual({ x: 530, y: 440 });
  expect(state.nodes.get("a")!.homeX).toBe(300);
  releaseGraphPhysicsNode(state, "a");
  run(state);
  expect(state.nodes.get("a")!.pinned).toBe(false);
  expect(distance(point(state, "a"), { x: 300, y: 300 })).toBeLessThan(2);
  expect(state.active).toBe(false);
});

test("dragging into a neighbor pushes the free neighbor away without moving the pin", () => {
  const state = createGraphPhysics(input([node("a", 200, 300, 24), node("b", 300, 300, 24)]));
  pinGraphPhysicsNode(state, "a", 280, 300);
  tickGraphPhysics(state, 16);
  expect(point(state, "a")).toEqual({ x: 280, y: 300 });
  expect(state.nodes.get("b")!.x).toBeGreaterThan(330);
  expect(state.nodes.get("b")!.homeX).toBe(300);
  releaseGraphPhysicsNode(state, "a");
  run(state);
  expect(Math.abs(state.nodes.get("b")!.x - 300)).toBeLessThan(5);
});

test("new neighborhood nodes burst from their hub while old positions and unrelated homes remain unchanged at sync", () => {
  const base = input([node("hub", 450, 400), node("unrelated", 800, 650)]);
  const state = createGraphPhysics(base);
  const old = [...state.nodes.values()].map(value => ({ id: value.id, ...point(state, value.id), homeX: value.homeX, homeY: value.homeY }));
  const expanded = input([...base.nodes, { ...node("child-a", 30, 30), hubId: "hub" }, { ...node("child-b", 950, 40), hubId: "hub" }],
    { links: [{ source: "hub", target: "child-a" }, { source: "hub", target: "child-b" }], expandedHubId: "hub", expandedHubIds: ["hub"] });
  syncGraphPhysics(state, expanded);
  for (const previous of old) expect(point(state, previous.id)).toEqual({ x: previous.x, y: previous.y });
  expect(state.nodes.get("unrelated")!.homeX).toBe(800);
  const child = state.nodes.get("child-a")!, hub = state.nodes.get("hub")!;
  const before = distance(child, hub);
  expect(before).toBeLessThan(70);
  expect(Math.hypot(child.vx, child.vy)).toBeGreaterThan(100);
  expect(child.hubId).toBe("hub");
  expect(hub.homeX).toBe(450);
  expect(hub.homeY).toBe(400);
  run(state, 20);
  expect(distance(child, hub)).toBeGreaterThan(before + 35);
  expect(distance(child, { x: 30, y: 30 })).toBeGreaterThan(100);
});

test("expanding with no hidden data still creates a hub and smoothly reorbits its existing neighborhood", () => {
  const base = input([node("hub", 450, 400), node("a", 250, 400), node("b", 650, 400), node("unrelated", 850, 650)],
    { links: [{ source: "hub", target: "a" }, { source: "hub", target: "b" }] });
  const state = createGraphPhysics(base), before = point(state, "a");
  syncGraphPhysics(state, { ...base, expandedHubId: "hub", expandedHubIds: ["hub"] });
  expect(point(state, "a")).toEqual(before);
  expect(state.nodes.get("a")!.hubId).toBe("hub");
  expect(Math.hypot(state.nodes.get("a")!.vx, state.nodes.get("a")!.vy)).toBeGreaterThan(30);
  expect(state.nodes.get("unrelated")!.homeX).toBe(850);
  tickGraphPhysics(state, 16);
  expect(distance(point(state, "a"), before)).toBeGreaterThan(.1);
});

test("topology and LOD sync keep old positions, rest offsets and spring lengths despite changed static layout", () => {
  const base = input([node("hub", 300, 300), { ...node("child", 520, 300), hubId: "hub" }],
    { links: [{ source: "hub", target: "child" }], expandedHubIds: ["hub"] });
  const state = createGraphPhysics(base);
  const old = { ...state.nodes.get("child")! }, length = state.links[0].length;
  syncGraphPhysics(state, { ...base, nodes: [{ ...node("hub", 900, 700), collision: { width: 160, height: 90, offsetY: 20 } },
    { ...node("child", 90, 600), hubId: "hub", collision: { width: 180, height: 100, offsetY: 20 } }] });
  const child = state.nodes.get("child")!;
  expect(point(state, "child")).toEqual({ x: old.x, y: old.y });
  expect([child.homeX, child.homeY, child.hubOffsetX, child.hubOffsetY]).toEqual([old.homeX, old.homeY, old.hubOffsetX, old.hubOffsetY]);
  expect(state.links[0].length).toBe(length);
  expect(child.collision.width).toBe(180);
  expect(state.active).toBe(true);
});

test("collapse clears a visible former hub's ownership without teleporting the surviving nodes", () => {
  const base = input([node("hub", 450, 400), node("child", 650, 400)], { links: [{ source: "hub", target: "child" }] });
  const state = createGraphPhysics(base);
  syncGraphPhysics(state, { ...base, expandedHubId: "hub", expandedHubIds: ["hub"] });
  run(state, 10);
  const before = point(state, "child");
  syncGraphPhysics(state, { ...base, expandedHubIds: [] });
  expect(point(state, "child")).toEqual(before);
  expect(state.nodes.get("child")!.hubId).toBeUndefined();
  expect(state.nodes.get("hub")!.expanded).toBe(false);
});

test("collapsing one hub can restore another active owner while preserving the child's current position", () => {
  const base = input([node("one", 250, 350), node("two", 600, 350), node("child", 450, 550)]);
  const state = createGraphPhysics(base);
  syncGraphPhysics(state, { ...base, expandedHubId: "two", expandedHubIds: ["one", "two"],
    nodes: [base.nodes[0], base.nodes[1], { ...base.nodes[2], hubId: "two" }] });
  const before = point(state, "child");
  syncGraphPhysics(state, { ...base, expandedHubIds: ["one"], nodes: [base.nodes[0], base.nodes[1], { ...base.nodes[2], hubId: "one" }] });
  expect(point(state, "child")).toEqual(before);
  expect(state.nodes.get("child")!.hubId).toBe("one");
});

test("card collisions use full rectangles and offsets instead of dot radii", () => {
  const state = createGraphPhysics(input([
    { ...node("a", 400, 350, 4), collision: { width: 180, height: 110, offsetY: 25 } },
    { ...node("b", 430, 360, 4), collision: { width: 180, height: 110, offsetY: 25 } },
  ]));
  settleGraphPhysics(state);
  const a = state.nodes.get("a")!, b = state.nodes.get("b")!;
  expect(Math.abs(a.x - b.x) >= 180 || Math.abs(a.y - b.y) >= 110).toBe(true);
  expect(state.active).toBe(false);
});

test("long pauses clamp the timestep and invalid input never propagates non-finite motion", () => {
  const state = createGraphPhysics(input([node("a", 300, 300)]));
  pinGraphPhysicsNode(state, "a", 650, 300); releaseGraphPhysicsNode(state, "a");
  const before = point(state, "a");
  tickGraphPhysics(state, 1_000_000);
  expect(state.elapsedMs).toBe(50);
  expect(distance(point(state, "a"), before)).toBeLessThan(40);
  tickGraphPhysics(state, Number.NaN);
  for (const node of state.nodes.values()) {
    expect([node.x, node.y, node.vx, node.vy].every(Number.isFinite)).toBe(true);
    expect(node.x).toBeGreaterThanOrEqual(0); expect(node.x).toBeLessThanOrEqual(state.width);
    expect(node.y).toBeGreaterThanOrEqual(0); expect(node.y).toBeLessThanOrEqual(state.height);
  }
});

test("reduced motion settles synchronously with no continuing animation or burst velocity", () => {
  const state = createGraphPhysics(input([node("hub", 400, 350)], { reducedMotion: true }));
  syncGraphPhysics(state, input([node("hub", 400, 350), { ...node("child", 10, 10), hubId: "hub" }],
    { expandedHubId: "hub", expandedHubIds: ["hub"], reducedMotion: true }));
  expect(state.active).toBe(false);
  expect(tickGraphPhysics(state, 16)).toBe(false);
  expect([...state.nodes.values()].every(node => node.vx === 0 && node.vy === 0)).toBe(true);
  expect(distance(state.nodes.get("hub")!, state.nodes.get("child")!)).toBeGreaterThan(100);
});

test("engine is deterministic, deduplicates link forces, breaks ownership cycles and can be stopped", () => {
  const config = input([{ ...node("a", 300, 300), hubId: "b" }, { ...node("b", 500, 300), hubId: "a" }],
    { links: [{ source: "a", target: "b" }, { source: "b", target: "a" }] });
  const first = createGraphPhysics(config), second = createGraphPhysics(config);
  run(first, 60); run(second, 60);
  expect([...first.nodes.values()]).toEqual([...second.nodes.values()]);
  expect(first.links).toHaveLength(1);
  expect([...first.nodes.values()].some(node => !node.hubId)).toBe(true);
  stopGraphPhysics(first);
  const before = point(first, "a");
  expect(tickGraphPhysics(first, 1000)).toBe(false);
  expect(point(first, "a")).toEqual(before);
});

test("maximum expansion-size graphs stop within the simulation budget and keep every body finite and bounded", () => {
  const nodes = Array.from({ length: 72 }, (_, index) => ({ ...node(`n-${index}`, 80 + index % 9 * 100, 70 + Math.floor(index / 9) * 90, 10),
    collision: { width: 44, height: 48, offsetY: 5 } }));
  const state = createGraphPhysics(input(nodes, { links: nodes.slice(1).map(value => ({ source: "n-0", target: value.id })) }));
  pinGraphPhysicsNode(state, "n-0", 500, 350); releaseGraphPhysicsNode(state, "n-0");
  run(state, 500);
  expect(state.active).toBe(false);
  expect(state.elapsedMs).toBeLessThanOrEqual(6500);
  for (const node of state.nodes.values()) {
    expect([node.x, node.y, node.vx, node.vy].every(Number.isFinite)).toBe(true);
    expect(node.x - node.collision.width / 2).toBeGreaterThanOrEqual(0);
    expect(node.x + node.collision.width / 2).toBeLessThanOrEqual(state.width);
    expect(node.y + node.collision.offsetY - node.collision.height / 2).toBeGreaterThanOrEqual(0);
    expect(node.y + node.collision.offsetY + node.collision.height / 2).toBeLessThanOrEqual(state.height);
  }
});

for (const location of [{ x: 55, y: 400 }, { x: 55, y: 55 }]) {
  test(`a boundary hub at ${location.x},${location.y} springs inward and its neighborhood surrounds it without an initial teleport`, () => {
    const base = input([node("hub", location.x, location.y, 25), node("unrelated", 850, 650)]);
    const state = createGraphPhysics(base), before = point(state, "hub");
    const children = Array.from({ length: 14 }, (_, index) => ({ ...node(`child-${index}`, 900, 700, 12), hubId: "hub",
      collision: { width: 100, height: 60 } }));
    syncGraphPhysics(state, { ...base, nodes: [...base.nodes, ...children], expandedHubId: "hub", expandedHubIds: ["hub"],
      links: children.map(child => ({ source: "hub", target: child.id })) });
    expect(point(state, "hub")).toEqual(before);
    expect(state.nodes.get("hub")!.homeX).toBeGreaterThan(250);
    expect(point(state, "unrelated")).toEqual({ x: 850, y: 650 });
    expect(Math.min(...state.links.map(link => link.length))).toBeGreaterThanOrEqual(169);
    expect(Math.max(...state.links.map(link => link.length))).toBeLessThanOrEqual(236);
    run(state);
    const hub = state.nodes.get("hub")!;
    const neighbors = children.map(child => state.nodes.get(child.id)!);
    expect(hub.x).toBeGreaterThan(180);
    expect(neighbors.filter(child => child.x < hub.x - 25).length).toBeGreaterThanOrEqual(2);
    expect(neighbors.filter(child => child.x > hub.x + 25).length).toBeGreaterThanOrEqual(2);
    expect(neighbors.filter(child => child.y < hub.y - 25).length).toBeGreaterThanOrEqual(2);
    expect(neighbors.filter(child => child.y > hub.y + 25).length).toBeGreaterThanOrEqual(2);
    expect(new Set(neighbors.map(child => Math.round(child.x))).size).toBeGreaterThan(6);
    expect(state.active).toBe(false);
    for (const child of neighbors) {
      expect([child.x, child.y, child.vx, child.vy].every(Number.isFinite)).toBe(true);
      expect(child.x - child.collision.width / 2).toBeGreaterThanOrEqual(0);
      expect(child.x + child.collision.width / 2).toBeLessThanOrEqual(state.width);
      expect(child.y - child.collision.height / 2).toBeGreaterThanOrEqual(0);
      expect(child.y + child.collision.height / 2).toBeLessThanOrEqual(state.height);
    }
  });
}
