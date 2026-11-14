/** Small deterministic graph physics. Coordinates are graph-world pixels; time is milliseconds. */
export interface PhysicsCollision {
  width: number;
  height: number;
  offsetX?: number;
  offsetY?: number;
}
export interface PhysicsNodeInput {
  id: string;
  x: number;
  y: number;
  radius: number;
  hubId?: string;
  collision?: PhysicsCollision;
}
export interface PhysicsLinkInput {
  source: string;
  target: string;
  length?: number;
  strength?: number;
}
export interface PhysicsNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  homeX: number;
  homeY: number;
  radius: number;
  hubId?: string;
  hubOffsetX: number;
  hubOffsetY: number;
  collision: Required<PhysicsCollision>;
  pinned: boolean;
  expanded: boolean;
}
export interface PhysicsLink {
  source: string;
  target: string;
  length: number;
  strength: number;
}
export interface GraphPhysicsState {
  nodes: Map<string, PhysicsNode>;
  links: PhysicsLink[];
  width: number;
  height: number;
  active: boolean;
  reducedMotion: boolean;
  elapsedMs: number;
  quietFrames: number;
}
export interface GraphPhysicsInput {
  nodes: readonly PhysicsNodeInput[];
  links: readonly PhysicsLinkInput[];
  width: number;
  height: number;
  reducedMotion?: boolean;
  /** New entities burst; existing direct neighbors smoothly orbit this new hub. */
  expandedHubId?: string;
  /** Current expanded hubs, so a collapse can clear ownership without resetting positions. */
  expandedHubIds?: readonly string[];
}

const finite = (value: number, fallback: number) => Number.isFinite(value) ? value : fallback;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const pairKey = (a: string, b: string) => JSON.stringify([a, b].sort());
const collisionFor = (input: PhysicsNodeInput, width: number, height: number): Required<PhysicsCollision> => ({
  width: clamp(finite(input.collision?.width ?? input.radius * 2, 32), 4, width - 24),
  height: clamp(finite(input.collision?.height ?? input.radius * 2, 32), 4, height - 24),
  offsetX: finite(input.collision?.offsetX ?? 0, 0), offsetY: finite(input.collision?.offsetY ?? 0, 0),
});
function bound(state: GraphPhysicsState, node: PhysicsNode) {
  const box = node.collision;
  node.x = clamp(finite(node.x, state.width / 2), 12 + box.width / 2 - box.offsetX, state.width - 12 - box.width / 2 - box.offsetX);
  node.y = clamp(finite(node.y, state.height / 2), 12 + box.height / 2 - box.offsetY, state.height - 12 - box.height / 2 - box.offsetY);
}
function wake(state: GraphPhysicsState) { state.active = true; state.elapsedMs = 0; state.quietFrames = 0; }
function angleFor(id: string) {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 16777619);
  return (hash >>> 0) / 4294967296 * Math.PI * 2;
}
function target(state: GraphPhysicsState, node: PhysicsNode) {
  const hub = node.hubId ? state.nodes.get(node.hubId) : undefined;
  return hub ? { x: hub.x + node.hubOffsetX, y: hub.y + node.hubOffsetY } : { x: node.homeX, y: node.homeY };
}

export function createGraphPhysics(input: GraphPhysicsInput): GraphPhysicsState {
  const state: GraphPhysicsState = { nodes: new Map(), links: [], width: 128, height: 128,
    active: false, reducedMotion: input.reducedMotion ?? false, elapsedMs: 0, quietFrames: 0 };
  syncGraphPhysics(state, input);
  return state;
}

/** Sync topology/LOD without teleporting old nodes or overwriting their resting offsets. */
export function syncGraphPhysics(state: GraphPhysicsState, input: GraphPhysicsInput) {
  const previousLinks = new Map(state.links.map(link => [pairKey(link.source, link.target), link]));
  state.width = Math.max(128, finite(input.width, 128));
  state.height = Math.max(128, finite(input.height, 128));
  if (input.reducedMotion !== undefined) state.reducedMotion = input.reducedMotion;
  const keep = new Set(input.nodes.map(node => node.id));
  for (const node of state.nodes.values()) if (node.hubId && !keep.has(node.hubId)) {
    const rest = target(state, node);
    node.homeX = rest.x; node.homeY = rest.y; node.hubId = undefined;
  }
  for (const id of state.nodes.keys()) if (!keep.has(id)) state.nodes.delete(id);
  const hub = input.expandedHubId ? state.nodes.get(input.expandedHubId) : undefined;
  const ancestors = new Set<string>();
  for (let parent = hub?.hubId ? state.nodes.get(hub.hubId) : undefined; parent && !ancestors.has(parent.id);
    parent = parent.hubId ? state.nodes.get(parent.hubId) : undefined) ancestors.add(parent.id);
  if (input.expandedHubIds) for (const node of state.nodes.values()) node.expanded = input.expandedHubIds.includes(node.id);
  if (hub) {
    hub.homeX = hub.x; hub.homeY = hub.y; hub.hubId = undefined; hub.expanded = true;
  }
  const added: PhysicsNode[] = [];
  for (const value of input.nodes) {
    if (!value.id) continue;
    const old = state.nodes.get(value.id);
    if (old) {
      old.radius = Math.max(2, finite(value.radius, old.radius));
      old.collision = collisionFor(value, state.width, state.height);
      continue;
    }
    const node: PhysicsNode = { id: value.id, x: finite(value.x, state.width / 2), y: finite(value.y, state.height / 2),
      vx: 0, vy: 0, homeX: finite(value.x, state.width / 2), homeY: finite(value.y, state.height / 2),
      radius: Math.max(2, finite(value.radius, 16)), hubOffsetX: 0, hubOffsetY: 0,
      collision: collisionFor(value, state.width, state.height), pinned: false, expanded: input.expandedHubIds?.includes(value.id) ?? false,
      hubId: value.hubId || (hub ? hub.id : undefined) };
    state.nodes.set(node.id, node); added.push(node);
  }
  const addedIds = new Set(added.map(node => node.id));
  for (const value of input.nodes) {
    const node = state.nodes.get(value.id)!;
    if (!node || addedIds.has(node.id) || node.expanded) continue;
    // An explicit active-hub list makes absence meaningful on collapse. Preserve
    // the old world-space rest position while changing who owns that rest offset.
    if (input.expandedHubIds === undefined && value.hubId === undefined) continue;
    const incoming = value.hubId && value.hubId !== node.id && state.nodes.has(value.hubId) ? value.hubId : undefined;
    if (incoming !== node.hubId) {
      const rest = target(state, node);
      node.homeX = rest.x; node.homeY = rest.y; node.hubId = incoming;
      const parent = incoming ? state.nodes.get(incoming) : undefined;
      if (parent) { node.hubOffsetX = rest.x - parent.x; node.hubOffsetY = rest.y - parent.y; }
    }
  }
  const byHub = new Map<string, PhysicsNode[]>();
  for (const node of added) {
    const parent = node.hubId ? state.nodes.get(node.hubId) : undefined;
    bound(state, node); node.homeX = node.x; node.homeY = node.y;
    if (!parent || parent.id === node.id) { node.hubId = undefined; continue; }
    if (hub) {
      const siblings = byHub.get(parent.id) || [];
      siblings.push(node); byHub.set(parent.id, siblings);
    } else {
      node.hubOffsetX = node.homeX - parent.x; node.hubOffsetY = node.homeY - parent.y;
    }
  }
  if (hub) {
    const neighbors = new Set(input.links.flatMap(link => link.source === hub.id ? [link.target] : link.target === hub.id ? [link.source] : []));
    for (const value of input.nodes) if (value.hubId === hub.id) neighbors.add(value.id);
    const orbit = byHub.get(hub.id) || [];
    for (const id of [...neighbors].sort()) {
      const node = state.nodes.get(id);
      if (!node || node.id === hub.id || node.expanded || node.pinned || ancestors.has(node.id) || addedIds.has(node.id)) continue;
      node.hubId = hub.id; orbit.push(node);
    }
    byHub.set(hub.id, orbit);
  }
  for (const [hubId, unsorted] of byHub) {
    const parent = state.nodes.get(hubId)!;
    const siblings = unsorted.sort((a, b) => a.id.localeCompare(b.id));
    const halfWidth = Math.max(0, ...siblings.map(node => node.collision.width / 2 + Math.abs(node.collision.offsetX)));
    const halfHeight = Math.max(0, ...siblings.map(node => node.collision.height / 2 + Math.abs(node.collision.offsetY)));
    const fittingRadius = Math.max(30, Math.min(state.width / 2 - halfWidth - 12, state.height / 2 - halfHeight - 12));
    const distances = siblings.map((node, index) => Math.min(fittingRadius,
      Math.max(170, (parent.collision.width + node.collision.width) / 2 + 45) + Math.floor(index / 8) * 65));
    const orbitRadius = Math.max(0, ...distances);
    // A hub near a boundary needs room for its complete neighborhood. Change its
    // spring target, never its current position; children burst from where it is
    // now and settle around where it will be, instead of clamping into a wall stack.
    if (parent.id === input.expandedHubId) {
      const marginX = Math.min(state.width / 2, orbitRadius + halfWidth + 12);
      const marginY = Math.min(state.height / 2, orbitRadius + halfHeight + 12);
      parent.homeX = clamp(parent.homeX, marginX, state.width - marginX);
      parent.homeY = clamp(parent.homeY, marginY, state.height - marginY);
    }
    const restCenter = parent.id === input.expandedHubId ? { x: parent.homeX, y: parent.homeY } : { x: parent.x, y: parent.y };
    siblings.forEach((node, index) => {
      const previousPosition = { x: node.x, y: node.y };
      const ring = Math.floor(index / 8), count = Math.min(8, siblings.length - ring * 8);
      const angle = angleFor(hubId) + index % 8 * Math.PI * 2 / count;
      const restDistance = distances[index];
      node.x = restCenter.x + Math.cos(angle) * restDistance;
      node.y = restCenter.y + Math.sin(angle) * restDistance;
      bound(state, node);
      node.homeX = node.x; node.homeY = node.y;
      node.hubOffsetX = node.homeX - restCenter.x; node.hubOffsetY = node.homeY - restCenter.y;
      if (!addedIds.has(node.id)) {
        node.x = previousPosition.x; node.y = previousPosition.y;
        if (!state.reducedMotion) { node.vx += Math.cos(angle) * 55; node.vy += Math.sin(angle) * 55; }
      } else if (!state.reducedMotion) {
        const seedDistance = Math.min(restDistance * .3, 50 + ring * 12);
        node.x = parent.x + Math.cos(angle) * seedDistance;
        node.y = parent.y + Math.sin(angle) * seedDistance;
        node.vx = Math.cos(angle) * 180; node.vy = Math.sin(angle) * 180;
        bound(state, node);
      }
    });
  }
  // Malformed parent cycles cannot create a moving-target feedback loop.
  for (const node of state.nodes.values()) {
    const seen = new Set([node.id]);
    let parent = node.hubId ? state.nodes.get(node.hubId) : undefined;
    while (parent) {
      if (seen.has(parent.id)) { node.hubId = undefined; break; }
      seen.add(parent.id); parent = parent.hubId ? state.nodes.get(parent.hubId) : undefined;
    }
  }
  const links = new Map<string, PhysicsLink>();
  for (const value of input.links) {
    const from = state.nodes.get(value.source), to = state.nodes.get(value.target);
    if (!from || !to || from.id === to.id) continue;
    const id = pairKey(from.id, to.id);
    if (links.has(id)) continue;
    const previous = previousLinks.get(id), a = target(state, from), b = target(state, to);
    const orbitChild = hub && from.id === hub.id && to.hubId === hub.id ? to
      : hub && to.id === hub.id && from.hubId === hub.id ? from : undefined;
    const rest = Math.max(40, orbitChild ? Math.hypot(orbitChild.hubOffsetX, orbitChild.hubOffsetY) : Math.hypot(a.x - b.x, a.y - b.y));
    // Explicit local re-orbits use their intended hub-relative lengths. Ordinary
    // topology/LOD sync still keeps old link rest lengths, including unrelated links.
    links.set(id, { source: from.id, target: to.id, length: clamp(finite(value.length ?? (orbitChild ? rest : previous?.length) ?? rest, rest), 20, 2000),
      strength: clamp(finite(value.strength ?? previous?.strength ?? 3.5, 3.5), 0, 30) });
  }
  state.links = [...links.values()].sort((a, b) => pairKey(a.source, a.target).localeCompare(pairKey(b.source, b.target)));
  wake(state);
  if (state.reducedMotion) settleGraphPhysics(state);
}

/** Pin only while actively dragging. Release wakes the spring back to its saved home/hub. */
export function pinGraphPhysicsNode(state: GraphPhysicsState, id: string, x: number, y: number) {
  const node = state.nodes.get(id);
  if (!node) return;
  node.pinned = true; node.x = finite(x, node.x); node.y = finite(y, node.y); node.vx = 0; node.vy = 0;
  bound(state, node); wake(state);
}
export function releaseGraphPhysicsNode(state: GraphPhysicsState, id: string) {
  const node = state.nodes.get(id);
  if (!node) return;
  node.pinned = false; node.vx = 0; node.vy = 0; wake(state);
  if (state.reducedMotion) settleGraphPhysics(state);
}
export function stopGraphPhysics(state: GraphPhysicsState) {
  state.active = false;
  for (const node of state.nodes.values()) { node.vx = 0; node.vy = 0; }
}

function simulate(state: GraphPhysicsState, seconds: number) {
  const nodes = [...state.nodes.values()].sort((a, b) => a.id.localeCompare(b.id));
  const forces = new Map(nodes.map(node => {
    const rest = target(state, node), spring = node.expanded ? 14 : 9;
    return [node.id, { x: (rest.x - node.x) * spring, y: (rest.y - node.y) * spring }];
  }));
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    let dx = b.x + b.collision.offsetX - a.x - a.collision.offsetX;
    let dy = b.y + b.collision.offsetY - a.y - a.collision.offsetY;
    if (Math.abs(dx) + Math.abs(dy) < .001) { dx = Math.cos(angleFor(a.id)); dy = Math.sin(angleFor(a.id)); }
    const distance = Math.max(1, Math.hypot(dx, dy));
    const repulsion = Math.min(1100, 450000 / Math.max(100, distance ** 2));
    const fx = dx / distance * repulsion, fy = dy / distance * repulsion;
    forces.get(a.id)!.x -= fx; forces.get(a.id)!.y -= fy;
    forces.get(b.id)!.x += fx; forces.get(b.id)!.y += fy;
  }
  for (const link of state.links) {
    const a = state.nodes.get(link.source)!, b = state.nodes.get(link.target)!;
    const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, Math.hypot(dx, dy));
    const pull = (distance - link.length) * link.strength;
    const fx = dx / distance * pull, fy = dy / distance * pull;
    forces.get(a.id)!.x += fx; forces.get(a.id)!.y += fy;
    forces.get(b.id)!.x -= fx; forces.get(b.id)!.y -= fy;
  }
  const damping = Math.exp(-7.5 * seconds);
  for (const node of nodes) if (!node.pinned) {
    const force = forces.get(node.id)!;
    node.vx = clamp(finite((node.vx + force.x * seconds) * damping, 0), -700, 700);
    node.vy = clamp(finite((node.vy + force.y * seconds) * damping, 0), -700, 700);
    node.x += node.vx * seconds; node.y += node.vy * seconds; bound(state, node);
  }
  // Project complete LOD boxes apart, transferring a small impulse to free neighbors.
  for (let pass = 0; pass < 3; pass++) for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    if (a.pinned && b.pinned) continue;
    const dx = b.x + b.collision.offsetX - a.x - a.collision.offsetX;
    const dy = b.y + b.collision.offsetY - a.y - a.collision.offsetY;
    const overlapX = (a.collision.width + b.collision.width) / 2 + 10 - Math.abs(dx);
    const overlapY = (a.collision.height + b.collision.height) / 2 + 10 - Math.abs(dy);
    if (overlapX <= 0 || overlapY <= 0) continue;
    const axis = overlapX < overlapY ? "x" : "y", overlap = axis === "x" ? overlapX : overlapY;
    const sign = (axis === "x" ? dx : dy) < 0 ? -1 : 1;
    const velocity = axis === "x" ? "vx" : "vy";
    const shareA = a.pinned ? 0 : b.pinned ? 1 : .5, shareB = b.pinned ? 0 : a.pinned ? 1 : .5;
    a[axis] -= sign * (overlap + .1) * shareA; b[axis] += sign * (overlap + .1) * shareB;
    a[velocity] -= sign * overlap * 2 * shareA; b[velocity] += sign * overlap * 2 * shareB;
    a[velocity] = clamp(a[velocity], -700, 700); b[velocity] = clamp(b[velocity], -700, 700);
    if (!a.pinned) bound(state, a);
    if (!b.pinned) bound(state, b);
  }
}

/** Return true while another animation frame is useful. Render once after the final false tick too. */
export function tickGraphPhysics(state: GraphPhysicsState, elapsedMs: number): boolean {
  if (!state.active) return false;
  if (state.reducedMotion) { settleGraphPhysics(state); return false; }
  const boundedMs = Math.min(6500 - state.elapsedMs, clamp(finite(elapsedMs, 16.667), 0, 50));
  if (boundedMs <= 0) {
    if (state.elapsedMs >= 6500) stopGraphPhysics(state);
    return state.active;
  }
  const steps = Math.max(1, Math.ceil(boundedMs / 16.667));
  let movement = 0;
  const before = new Map([...state.nodes.values()].map(node => [node.id, { x: node.x, y: node.y }]));
  for (let step = 0; step < steps; step++) simulate(state, boundedMs / steps / 1000);
  for (const node of state.nodes.values()) {
    const old = before.get(node.id)!;
    movement = Math.max(movement, Math.hypot(old.x - node.x, old.y - node.y));
  }
  state.elapsedMs += boundedMs;
  const speed = Math.max(0, ...[...state.nodes.values()].filter(node => !node.pinned).map(node => Math.hypot(node.vx, node.vy)));
  state.quietFrames = speed < 1.2 && movement < .08 ? state.quietFrames + 1 : 0;
  if (state.quietFrames >= 12 || state.elapsedMs >= 6500) stopGraphPhysics(state);
  return state.active;
}

/** Reduced-motion/static callers can settle synchronously without keeping an animation loop alive. */
export function settleGraphPhysics(state: GraphPhysicsState, maxSteps = 360) {
  for (let step = 0; step < clamp(finite(maxSteps, 360), 0, 600); step++) simulate(state, 1 / 60);
  stopGraphPhysics(state);
}
