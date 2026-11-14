import { ChevronDown, ExternalLink } from "lucide-react";
import type { Entity, InvestorKind, Source } from "../lib/types";

const CATEGORY_LABELS: Partial<Record<InvestorKind, string>> = {
  vc: "Venture capital", angel: "Angel investor", corporate_vc: "Corporate venture capital",
};
const FIELD_LABELS: Record<string, string> = {
  id: "Cala entity ID", lei: "LEI", cik: "CIK", website: "Website",
  headquarters_address: "Headquarters address", office_address: "Office address", registered_address: "Registered address",
  country: "Country", country_name: "Country", legal_name: "Legal name",
  founding_date: "Founding date", funding_stage: "Funding stage",
};

function fieldLabel(name: string): string {
  return FIELD_LABELS[name] || name.replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
}

function dateText(value: string): string {
  const parsed = new Date(value.includes("T") ? value : `${value}T12:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  }).format(parsed);
}

function publicUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch { return null; }
}

function hostname(url: string): string {
  try { return new URL(url).hostname; } catch { return url; }
}

function countryPresentation(entity: Entity) {
  const basis = (entity.country_basis || "").toLowerCase();
  const registration = basis.startsWith("registered") || basis.startsWith("registration");
  const office = basis.startsWith("office");
  const headquarters = basis.startsWith("headquarters") || basis === "country_property";
  const explanation = basis.includes("conflict") ? "Conflicting metadata; no country has been confirmed."
    : registration ? "Registration address fallback; headquarters unconfirmed."
      : office ? "Office address fallback; headquarters unconfirmed."
        : basis === "headquarters_relationship" ? "Based on the headquarters location relationship."
          : basis === "headquarters_address" ? "Based on the headquarters address."
            : basis === "country_property" ? "Based on Cala's country property."
              : headquarters ? "Based on headquarters metadata."
                : "Headquarters location is unconfirmed.";
  const summary = !entity.country ? "Headquarters country unknown"
    : registration ? `Country: ${entity.country} (registration address fallback)`
      : office ? `Country: ${entity.country} (office address fallback)`
        : headquarters ? `Headquarters: ${entity.country}`
          : `Country: ${entity.country} (headquarters unconfirmed)`;
  return { summary, label: headquarters && entity.country ? "Headquarters country" : "Country", explanation };
}

function MetadataSources({ sources }: { sources: Source[] }) {
  const unique = sources.filter((source, index) => sources.findIndex(other =>
    other.url === source.url && other.published_at === source.published_at && other.retrieved_at === source.retrieved_at) === index);
  if (!unique.length) return <small className="entity-metadata-unsourced">No source link returned for this field.</small>;
  return <ul className="entity-metadata-sources">{unique.map((source, index) => {
    const url = publicUrl(source.url);
    return <li key={`${source.url}-${index}`}>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">{source.label || hostname(url)}<ExternalLink size={11} /></a> : <span>{source.label || "Cala source"}</span>}
      <small>{source.published_at ? `Published ${dateText(source.published_at)}` : "Publication date unknown"} · Retrieved {dateText(source.retrieved_at)}</small>
    </li>;
  })}</ul>;
}

function MetadataValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="entity-metadata-empty">Not provided</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (typeof value === "number" || typeof value === "string") return <span>{String(value)}</span>;
  if (Array.isArray(value)) return value.length
    ? <ul className="entity-metadata-values">{value.map((item, index) => <li key={index}><MetadataValue value={item} /></li>)}</ul>
    : <span className="entity-metadata-empty">None returned</span>;
  if (typeof value === "object") return <dl className="entity-metadata-object">{Object.entries(value).map(([key, item]) =>
    <div key={key}><dt>{fieldLabel(key)}</dt><dd><MetadataValue value={item} /></dd></div>)}</dl>;
  return <span className="entity-metadata-empty">Not provided</span>;
}

/** Raw entity type and investor category are distinct Cala observations. */
export function EntityIdentity({ entity }: { entity: Entity }) {
  const category = CATEGORY_LABELS[entity.kind];
  const entityType = entity.entity_type || (entity.kind === "company" ? "Company" : null);
  const country = countryPresentation(entity);
  return <span className="entity-identity-meta">
    <span>{country.summary}</span>
    <span>Entity type: {entityType || "Unknown"}</span>
    {category && <span>Investor category: {category}</span>}
  </span>;
}

/** Full returned property metadata stays separate from the selection control. */
export default function EntityMetadata({ entity }: { entity: Entity }) {
  const metadata = entity.metadata || [];
  const website = publicUrl(entity.website);
  const country = countryPresentation(entity);
  const hasDetails = metadata.length > 0 || entity.headquarters_address || entity.office_address || entity.registered_address || entity.country_basis
    || entity.country_sources?.length || entity.kind_sources?.length || entity.metadata_status === "retrieved";
  if (!hasDetails) return entity.metadata_status === "unavailable"
    ? <p className="entity-metadata-status">Additional Cala details could not be retrieved. Available identity details are shown.</p> : null;
  const category = CATEGORY_LABELS[entity.kind];
  return <details className="entity-metadata">
    <summary>View Cala details<ChevronDown size={13} /></summary>
    <div className="entity-metadata-content">
      {entity.metadata_retrieved_at && <p className="entity-metadata-retrieved">Retrieved {dateText(entity.metadata_retrieved_at)} · Available Cala properties</p>}
      <dl className="entity-metadata-overview">
        {website && <div><dt>Website</dt><dd><a href={website} target="_blank" rel="noopener noreferrer">{hostname(website)}<ExternalLink size={11} /></a></dd></div>}
        <div><dt>Entity type</dt><dd>{entity.entity_type || (entity.kind === "company" ? "Company" : "Not provided")}</dd></div>
        <div><dt>Investor category</dt><dd>{category || "Unconfirmed"}{entity.kind_sources?.length ? <MetadataSources sources={entity.kind_sources} /> : null}</dd></div>
        {(entity.country || entity.country_basis || entity.country_sources?.length) && <div><dt>{country.label}</dt><dd>{entity.country || "Unknown"}
          <small> · {country.explanation}</small><MetadataSources sources={entity.country_sources || []} /></dd></div>}
        {entity.headquarters_address && <div><dt>Headquarters address</dt><dd>{entity.headquarters_address}</dd></div>}
        {entity.office_address && <div><dt>Office address</dt><dd>{entity.office_address}</dd></div>}
        {entity.registered_address && <div><dt>Registered address</dt><dd>{entity.registered_address}</dd></div>}
      </dl>
      {metadata.length > 0 && <div className="entity-metadata-properties"><h5>Returned properties</h5>
        <dl>{metadata.map((property, index) => <div className="entity-metadata-property" key={`${property.name}-${index}`}>
          <dt>{fieldLabel(property.name)}</dt><dd><MetadataValue value={property.value} /><MetadataSources sources={property.sources || []} /></dd>
        </div>)}</dl></div>}
      {entity.metadata_status === "unavailable" && <p className="entity-metadata-status">Additional Cala details could not be retrieved.</p>}
    </div>
  </details>;
}
