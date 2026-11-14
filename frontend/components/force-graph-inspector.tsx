"use client";

import { ExternalLink, X } from "lucide-react";
import { graphSources, type ResultGraphEdge, type ResultGraphNode } from "../lib/result-graph";
import type { RunResult, Source } from "../lib/types";
import EntityMetadata from "./entity-metadata";

export const graphKindName = (node: ResultGraphNode): string => node.card ? "Shortlisted investor" : ({
  founder: "Your company", lead: "Selected lead", investor: "Investor", company: "Company", person: "Person",
})[node.kind];

export function graphDate(value?: string | null, precision?: string | null): string {
  if (!value) return "Date unknown";
  const parsed = new Date(value.includes("T") ? value : `${value}T12:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return "Date unknown";
  const options: Intl.DateTimeFormatOptions = precision === "year" ? { year: "numeric" }
    : precision === "month" ? { month: "short", year: "numeric" } : { day: "numeric", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" }).format(parsed);
}

function rolePeriod(asOf: string, since?: string | null, until?: string | null, current = false): string {
  if (until) {
    const range = `${since ? graphDate(since) : "Start date unknown"} – ${graphDate(until)}`;
    if (until.slice(0, 10) < asOf.slice(0, 10)) return `${range} · ${current ? "Current status unconfirmed; reported dates conflict" : "Historical role"}`;
    return `${range} · ${current ? "Current, explicitly reported by Cala" : "Current status unconfirmed"}`;
  }
  if (current) return `${since ? `${graphDate(since)} – ` : ""}Current, explicitly reported by Cala`;
  return `${since ? `Started ${graphDate(since)}; end date unreported.` : "Role dates not supplied."} Current status unconfirmed.`;
}

function Evidence({ sources }: { sources: Source[] }) {
  const unique = graphSources(sources);
  const sourceItem = (source: Source, index: number) => <li key={`${source.url}-${index}`}>
    <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label || "Cala source"}<ExternalLink size={11} /></a>
    <span>{source.published_at ? `Published ${graphDate(source.published_at)}` : "Publication date unknown"} · Retrieved {graphDate(source.retrieved_at)}</span>
  </li>;
  if (!unique.length) return <p className="kg-evidence-empty">No public source link was returned for this detail.</p>;
  return <div className="kg-source-list"><ul>{unique.slice(0, 3).map(sourceItem)}</ul>{unique.length > 3 && <details>
    <summary>{unique.length - 3} more source links</summary><ul>{unique.slice(3).map(sourceItem)}</ul>
  </details>}</div>;
}

export default function GraphEvidenceInspector({ result, node, edge, nodes, edges, onChooseEdge, onExpand, expanded, onClose }: {
  result: RunResult; node?: ResultGraphNode; edge?: ResultGraphEdge; nodes: ResultGraphNode[]; edges: ResultGraphEdge[];
  onChooseEdge: (edge: ResultGraphEdge) => void; onExpand: (node: ResultGraphNode) => void; expanded: boolean; onClose: () => void;
}) {
  const byId = new Map(nodes.map(item => [item.id, item]));
  const edgeLabel = (item: ResultGraphEdge) => `${byId.get(item.source)?.label || "Entity"} → ${byId.get(item.target)?.label || "Entity"} · ${item.label}${item.funding ? ` · ${graphDate(item.funding.event_date, item.funding.event_date_precision)}` : ""}`;
  const incident = node ? edges.filter(item => item.source === node.id || item.target === node.id) : [];
  const connection = (item: ResultGraphEdge) => <li key={item.id}><button type="button" className="kg-connection-button" onClick={() => onChooseEdge(item)}>
    <span>{byId.get(item.source)?.label} → {byId.get(item.target)?.label}<small>{item.label}{item.funding ? ` · ${graphDate(item.funding.event_date, item.funding.event_date_precision)}` : ""}</small></span>
  </button></li>;
  return <div className="kg-inspector" aria-live="polite">
    <div className="kg-inspector-heading"><div><span className="kg-kicker">{edge ? edge.funding ? "Funding event evidence" : edge.role ? "Sourced affiliation" : edge.kind === "founder_route" ? "Founder-selected route" : "Professional relationship evidence" : node ? graphKindName(node) : "Evidence"}</span>
      <h3>{edge ? edgeLabel(edge) : node?.label}</h3></div><button type="button" className="kg-inspector-close" aria-label="Close graph details" onClick={onClose}><X size={15} /></button></div>
    {edge ? <>
      {edge.funding ? <>
        <p><strong>{byId.get(edge.source)?.label}</strong> participated in funding for <strong>{edge.funding.company.name}</strong>. The line represents this identified event.</p>
        <dl className="kg-evidence-facts"><div><dt>Stage</dt><dd>{edge.funding.stage?.replaceAll("_", " ") || "Unknown"}</dd></div>
          <div><dt>Event date</dt><dd>{graphDate(edge.funding.event_date, edge.funding.event_date_precision)}</dd></div>
          <div><dt>Recipient</dt><dd>{edge.funding.company.name}</dd></div>
          {edge.funding.total_amount_eur && <div><dt>Round total</dt><dd>{new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(Number(edge.funding.total_amount_eur))} · actual investor checks unknown</dd></div>}</dl>
        <p>Historical participation does not confirm an introduction, a personal relationship or interest in your current round.</p>
        <small className="kg-entity-id">Cala funding event: {edge.funding.id}</small>
      </> : edge.role ? <><p>{edge.label} · {edge.role.person.name} → {edge.role.org.name}</p>
        <p>{rolePeriod(result.confirmed_at, edge.role.valid_since, edge.role.valid_until, edge.role.current)}</p>
        <p>An organizational role does not confirm a personal relationship or introduction access.</p></>
        : edge.hop ? <><p>{edge.hop.org ? `Organizational context: ${edge.hop.org.name}. ` : ""}{rolePeriod(result.confirmed_at, edge.hop.since, edge.hop.until)}</p>
          <p>{edge.confirmed ? "This path includes a connection confirmed by the founder. Public evidence is listed below." : "Public professional evidence; personal relationship and introduction access are unconfirmed."}</p></>
          : <p>The founder selected {result.lead?.name} as their lead. Current-round commitment: {result.profile.round.lead_status.replaceAll("_", " ")}. This route is founder supplied.</p>}
      {edge.kind !== "founder_route" && <Evidence sources={edge.sources} />}
    </> : node ? <>
      <p>{node.degree} unique {node.degree === 1 ? "connection" : "connections"}{node.roles.length ? ` · ${node.roles.join(" · ")}` : ""}</p>
      <div className="kg-expansion-actions"><button type="button" onClick={() => onExpand(node)} aria-label={`${expanded ? "Collapse" : "Expand"} connections for ${node.label}`}>{expanded ? "Collapse connections" : "Expand connections"}</button>
        <span>{expanded ? "Explored hub" : node.availableNeighborCount ? `${node.availableNeighborCount} more sourced neighbors` : "Explore this neighborhood"}</span></div>
      <p>{node.kind === "founder" ? `${result.profile.startup.sector}${result.profile.startup.country ? ` · ${result.profile.startup.country}` : ""}. Company identity and selected lead were confirmed by the founder.`
        : node.kind === "person" ? node.entity?.description || "Cala identifies this person in retrieved role or funding records. Role dates and sources are available on the connected lines."
          : node.card ? `${result.scoring_version.startsWith("rules-") ? `${node.card.fit_score} rules met.` : `Introduction priority ${node.card.fit_score}/100.`} Select this investor in the shortlist to discuss them with the round assistant or open their full details.`
            : node.entity?.description || "An organization identified in the retrieved funding or professional-role evidence."}</p>
      {incident.length > 0 && <div className="kg-node-connections"><h4>Connections and evidence</h4><ul>{incident.slice(0, 5).map(connection)}</ul>{incident.length > 5 && <details><summary>{incident.length - 5} more connections</summary><ul>{incident.slice(5).map(connection)}</ul></details>}</div>}
      {node.calaId && <small className="kg-entity-id">Cala ID: {node.calaId}</small>}
      {node.entity && <EntityMetadata entity={node.entity} />}
      {node.sources.length > 0 && <Evidence sources={node.sources} />}
    </> : null}
  </div>;
}
