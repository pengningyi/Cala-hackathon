import type { RunResult } from "../lib/types";

const money = (value: string) => new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", maximumFractionDigits: 2,
}).format(Number(value));

/** Main's suggested ticket split, with its estimate basis kept visible. */
export default function AllocationPanel({ result }: { result: RunResult }) {
  const rows = result.allocation || [];
  if (!rows.length) return null;
  return <section className="workspace-allocation" aria-labelledby="allocation-heading">
    <div className="workspace-allocation-heading"><div><span className="eyebrow">Suggested round planning</span>
      <h2 id="allocation-heading">Estimated allocation</h2></div>
      <p>Shares of the {money(result.remaining_allocation_eur)} open allocation</p></div>
    <div className="workspace-allocation-rows">{rows.map((row, index) => {
      const percentage = Math.min(100, Math.max(0, Number.isFinite(row.percentage) ? row.percentage : 0));
      const open = row.investor_id === null;
      return <div className={`workspace-allocation-row${open ? " is-open" : ""}`} key={`${row.investor_id || "open"}-${index}`}>
        <div className="workspace-allocation-name"><strong>{row.name}</strong><small>{open ? "Unallocated" : "Historical equal-share estimate"}</small></div>
        <div className="workspace-allocation-track" role="progressbar" aria-label={`${row.name}: ${open ? "unallocated" : "estimated"} share of open allocation`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage} aria-valuetext={`${percentage.toFixed(1)}%`}><span style={{ width: `${percentage}%` }} /></div>
        <div className="workspace-allocation-amount"><strong>{money(row.amount_eur)}</strong><span>{percentage.toFixed(1)}%</span></div>
      </div>;
    })}</div>
    <p className="workspace-allocation-note">Illustrative split using historical EUR round totals divided by participant counts. Actual individual checks and willingness to invest are unconfirmed. Percentages describe the open allocation, not investment likelihood.</p>
  </section>;
}
