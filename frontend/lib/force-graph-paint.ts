import { forceGraphDetail, type CanvasNode } from "./force-graph-data";

export interface GraphPaintState {
  selectedId?: string | null;
  hoveredId?: string | null;
  neighborIds?: ReadonlySet<string>;
  expandedIds?: ReadonlySet<string>;
}

const FONT = 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif';
const TYPES = { founder: "Your company", lead: "Selected lead", investor: "Investor", company: "Company", person: "Person" };
const clamp = (n: number, min: number, max: number) => Math.max(min, Math.min(max, n));

/** Unique-neighbor degree controls bodies, rather than the number of event records. */
export function nodeWorldRadius(node: CanvasNode): number {
  return 4.5 + Math.sqrt(Math.max(0, node.degree)) * 1.15;
}

function shape(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number, person: boolean) {
  ctx.beginPath();
  if (person) ctx.roundRect(x - radius, y - radius, radius * 2, radius * 2, radius * .2);
  else ctx.arc(x, y, radius, 0, Math.PI * 2);
}

function palette(node: CanvasNode, active: boolean) {
  if (node.kind === "founder") return { fill: "#302c39", stroke: "#302c39", ink: "#ffffff" };
  if (active) return { fill: "#8057a3", stroke: "#704692", ink: "#ffffff" };
  if (node.kind === "lead") return { fill: "#e9def2", stroke: "#a386b9", ink: "#70538b" };
  if (node.kind === "person") return { fill: "#f3edf9", stroke: "#b39ac7", ink: "#86649e" };
  if (node.kind === "investor") return { fill: "#e9e0f3", stroke: "#a282bd", ink: "#765090" };
  return { fill: "#f6f3f9", stroke: "#b5a9c3", ink: "#8c7b9d" };
}

function ellipsize(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let low = 0, high = text.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (ctx.measureText(`${text.slice(0, mid)}…`).width <= maxWidth) low = mid;
    else high = mid - 1;
  }
  return `${text.slice(0, low).trimEnd()}…`;
}

function textLines(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, count: number): string[] {
  const words = text.trim().split(/\s+/), lines: string[] = [];
  while (words.length && lines.length < count) {
    if (lines.length === count - 1) {
      lines.push(ellipsize(ctx, words.join(" "), maxWidth));
      break;
    }
    let line = words.shift() || "";
    if (ctx.measureText(line).width > maxWidth) {
      lines.push(ellipsize(ctx, line, maxWidth));
      continue;
    }
    while (words.length && ctx.measureText(`${line} ${words[0]}`).width <= maxWidth) line += ` ${words.shift()}`;
    lines.push(line);
  }
  return lines;
}

/** Code-native icons do not need image loading or make additional network requests. */
function icon(ctx: CanvasRenderingContext2D, person: boolean, x: number, y: number, size: number, color: string) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(size / 16, size / 16);
  ctx.lineWidth = 1.25;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = color;
  ctx.beginPath();
  if (person) {
    ctx.arc(0, -3.2, 2.5, 0, Math.PI * 2);
    ctx.moveTo(-4.3, 5.1);
    ctx.bezierCurveTo(-4.3, -.1, 4.3, -.1, 4.3, 5.1);
  } else {
    ctx.rect(-4.5, -5.5, 9, 11);
    ctx.moveTo(-1.5, 5.5); ctx.lineTo(-1.5, 2.5); ctx.lineTo(1.5, 2.5); ctx.lineTo(1.5, 5.5);
    for (const px of [-1.9, 1.9]) for (const py of [-2.7, .1]) { ctx.moveTo(px - .3, py); ctx.lineTo(px + .3, py); }
  }
  ctx.stroke();
  ctx.restore();
}

function dimensions(node: CanvasNode, full: boolean) {
  const degree = Math.sqrt(Math.max(0, node.degree));
  return { width: full ? Math.min(190, 158 + degree * 5) : Math.min(168, 140 + degree * 3), height: full ? 74 : 32 };
}

function iconLabel(ctx: CanvasRenderingContext2D, node: CanvasNode) {
  ctx.font = `500 12px ${FONT}`;
  const lines = textLines(ctx, node.name, 174, 3);
  return { lines, width: Math.max(0, ...lines.map(line => ctx.measureText(line).width)) + 8, height: lines.length * 15 + 4 };
}

export function paintGraphNode(node: CanvasNode, ctx: CanvasRenderingContext2D, globalScale: number, state: GraphPaintState = {}) {
  if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
  const k = Math.max(.1, globalScale);
  const person = node.entityType === "person", selected = node.id === state.selectedId;
  const hovered = node.id === state.hoveredId, neighbor = !!state.neighborIds?.has(node.id);
  const expanded = !!state.expandedIds?.has(node.id), active = selected || hovered;
  const level = active && k < 1.5 ? "short" : forceGraphDetail(k);
  const colors = palette(node, active), radius = nodeWorldRadius(node);
  ctx.save();
  ctx.translate(node.x, node.y);
  ctx.globalAlpha = 1;
  if (level === "marker" || level === "icon") {
    if (selected || hovered || neighbor || expanded) {
      shape(ctx, 0, 0, radius + (active ? 3.2 : 2) / k, person);
      ctx.fillStyle = active ? "#e9def6" : "#f0e9f8";
      ctx.fill();
      if (selected || expanded) { ctx.strokeStyle = active ? "#a781c2" : "#c4afd7"; ctx.lineWidth = .8 / k; ctx.stroke(); }
    }
    shape(ctx, 0, 0, radius, person);
    ctx.fillStyle = colors.fill;
    ctx.fill();
    ctx.strokeStyle = colors.stroke;
    ctx.lineWidth = (selected ? 1.4 : 1) / k;
    ctx.stroke();
    if (level === "icon") {
      icon(ctx, person, 0, 0, radius * 1.25, colors.ink);
      // Names appear as soon as entities become icons, without requiring hover.
      // Keep type shapes and degree sizing; labels remain a readable screen size.
      ctx.save();
      ctx.scale(1 / k, 1 / k);
      const label = iconLabel(ctx, node), top = radius * k + 7;
      ctx.beginPath();
      ctx.roundRect(-label.width / 2, top - 2, label.width, label.height, 4);
      ctx.fillStyle = "#fcfbfeee";
      ctx.fill();
      ctx.fillStyle = "#554561";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      label.lines.forEach((line, index) => ctx.fillText(line, 0, top + index * 15));
      ctx.restore();
    }
    ctx.restore();
    return;
  }

  // Every dimension below is in screen pixels; only body/icon sizes encode degree.
  ctx.scale(1 / k, 1 / k);
  const full = level === "full", { width, height } = dimensions(node, full);
  if (active) { ctx.shadowColor = "#65467d21"; ctx.shadowBlur = 12; ctx.shadowOffsetY = 3; }
  ctx.beginPath();
  ctx.roundRect(-width / 2, -height / 2, width, height, full ? 10 : 8);
  ctx.fillStyle = selected ? "#faf6fe" : "#ffffff";
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.strokeStyle = active ? "#aa85c6" : neighbor || expanded ? "#c6b2da" : "#e1d9e9";
  ctx.lineWidth = selected ? 1.4 : .9;
  ctx.stroke();
  if (expanded) {
    ctx.beginPath();
    ctx.roundRect(-width / 2 - 3, -height / 2 - 3, width + 6, height + 6, full ? 13 : 11);
    ctx.strokeStyle = "#d7c7e7";
    ctx.lineWidth = .7;
    ctx.stroke();
  }
  const badgeRadius = full ? clamp(10 + Math.sqrt(node.degree) * .65, 10, 14) : clamp(8 + Math.sqrt(node.degree) * .4, 8, 11);
  const badgeX = -width / 2 + (full ? 22 : 17), badgeY = full ? -10 : 0;
  shape(ctx, badgeX, badgeY, badgeRadius, person);
  ctx.fillStyle = colors.fill;
  ctx.fill();
  ctx.strokeStyle = colors.stroke;
  ctx.lineWidth = .8;
  ctx.stroke();
  icon(ctx, person, badgeX, badgeY, full ? 15 : 13, colors.ink);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  const textX = -width / 2 + (full ? 43 : 33), maxWidth = width - (full ? 54 : 42);
  if (full) {
    ctx.font = `500 9px ${FONT}`;
    ctx.fillStyle = "#8b789c";
    ctx.fillText(TYPES[node.kind], textX, -23);
    ctx.font = `600 12px ${FONT}`;
    ctx.fillStyle = "#332d3d";
    const lines = textLines(ctx, node.name, maxWidth, 2);
    lines.forEach((line, index) => ctx.fillText(line, textX, -8 + index * 14));
    ctx.beginPath();
    ctx.moveTo(-width / 2 + 10, 16); ctx.lineTo(width / 2 - 10, 16);
    ctx.strokeStyle = "#f0ebf5";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.font = `400 9px ${FONT}`;
    ctx.fillStyle = "#897795";
    ctx.fillText(`${node.degree} ${node.degree === 1 ? "connection" : "connections"}`, -width / 2 + 11, 26);
    if (expanded) { ctx.textAlign = "right"; ctx.fillStyle = "#8a64a5"; ctx.fillText("Explored", width / 2 - 11, 26); }
  } else {
    ctx.font = `500 12px ${FONT}`;
    ctx.fillStyle = "#3f354c";
    ctx.fillText(ellipsize(ctx, node.name, maxWidth), textX, 0);
  }
  ctx.restore();
}

/** The pick canvas follows the visible LOD, with padding for touch and small dots. */
export function paintGraphNodeHit(node: CanvasNode, color: string, ctx: CanvasRenderingContext2D, globalScale: number, state: GraphPaintState = {}) {
  if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
  const k = Math.max(.1, globalScale), active = node.id === state.selectedId || node.id === state.hoveredId;
  const level = active && k < 1.5 ? "short" : forceGraphDetail(k);
  ctx.save();
  ctx.fillStyle = color;
  if (level === "marker" || level === "icon") {
    shape(ctx, node.x, node.y, Math.max(nodeWorldRadius(node) + 2 / k, 10 / k), node.entityType === "person");
    if (level === "icon") {
      const label = iconLabel(ctx, node), top = node.y + nodeWorldRadius(node) + 5 / k;
      ctx.rect(node.x - label.width / (2 * k), top, label.width / k, label.height / k);
    }
  } else {
    const { width, height } = dimensions(node, level === "full");
    ctx.beginPath();
    ctx.roundRect(node.x - (width + 6) / (2 * k), node.y - (height + 6) / (2 * k), (width + 6) / k, (height + 6) / k, 9 / k);
  }
  ctx.fill();
  ctx.restore();
}

/** Zoom-aware world grid; transform includes device pixel ratio as well as camera scale. */
export function paintGraphGrid(ctx: CanvasRenderingContext2D, globalScale: number) {
  const k = Math.max(.1, globalScale), transform = ctx.getTransform();
  if (!transform.a || !transform.d) return;
  const left = -transform.e / transform.a, right = (ctx.canvas.width - transform.e) / transform.a;
  const top = -transform.f / transform.d, bottom = (ctx.canvas.height - transform.f) / transform.d;
  const smooth = (value: number) => { const t = clamp(value, 0, 1); return t * t * (3 - 2 * t); };
  ctx.save();
  for (const spacing of [5, 15, 75, 375]) {
    const screenSpacing = spacing * k;
    const opacity = .4 * smooth((Math.log(screenSpacing) - Math.log(10)) / Math.log(2.4))
      * (1 - smooth((Math.log(screenSpacing) - Math.log(45)) / Math.log(2.8)));
    if (opacity < .015) continue;
    const x0 = Math.floor(left / spacing) * spacing, y0 = Math.floor(top / spacing) * spacing;
    const columns = Math.ceil((right - x0) / spacing) + 1, rows = Math.ceil((bottom - y0) / spacing) + 1;
    if (columns * rows > 12_000) continue;
    ctx.fillStyle = `rgba(176,154,197,${opacity.toFixed(3)})`;
    ctx.beginPath();
    for (let column = 0; column < columns; column++) for (let row = 0; row < rows; row++) {
      const x = x0 + column * spacing, y = y0 + row * spacing;
      ctx.moveTo(x + .7 / k, y);
      ctx.arc(x, y, .7 / k, 0, Math.PI * 2);
    }
    ctx.fill();
  }
  ctx.restore();
}
