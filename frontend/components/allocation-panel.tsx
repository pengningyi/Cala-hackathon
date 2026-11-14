import type { AllocationItem, CandidateCard, RunResult } from "../lib/types";

const money = (value: string) => new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", maximumFractionDigits: 2,
}).format(Number(value));

const share = (value: number) => Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0));

/** Allocation figures come from the saved plan, independently of investor scores. */
export function InvestorAllocation({ card, allocation }: { card: CandidateCard; allocation?: AllocationItem[] }) {
  const row = allocation?.find(item => item.investor_id === card.investor.id);
  if (!row) {
    const label = !allocation?.length ? "Allocation unavailable" : card.review_status === "flagged"
      || Number(card.estimated_ticket_eur) > 0 ? "Not allocated" : "Estimate unavailable";
    return <div className="workspace-investor-allocation is-unavailable"><span>{label}</span></div>;
  }
  const percentage = share(row.percentage);
  return <div className="workspace-investor-allocation">
    <span className="workspace-investor-allocation-value">{money(row.amount_eur)}</span>
    <span className="workspace-investor-allocation-share">{percentage.toFixed(1)}%</span>
    <div className="workspace-allocation-track" role="progressbar" aria-label={`${card.investor.name}: estimated share of open allocation`}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage} aria-valuetext={`${money(row.amount_eur)}, ${percentage.toFixed(1)}% of open allocation`}>
      <span style={{ width: `${percentage}%` }} /></div>
  </div>;
}

/** The unallocated remainder and estimate basis live inside the investor overview. */
export default function AllocationSummary({ result }: { result: RunResult }) {
  const rows = result.allocation || [];
  if (!rows.length) return null;
  return <div className="workspace-allocation-summary" aria-label="Allocation remainder and estimate basis">
    {rows.filter(row => row.investor_id === null).map((row, index) => {
      const percentage = share(row.percentage);
      return <div className="workspace-allocation-row is-open" key={`open-${index}`}>
        <div className="workspace-allocation-name"><strong>{row.name}</strong><small>Unallocated</small></div>
        <div className="workspace-allocation-track" role="progressbar" aria-label={`${row.name}: unallocated share of open allocation`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage} aria-valuetext={`${percentage.toFixed(1)}%`}><span style={{ width: `${percentage}%` }} /></div>
        <div className="workspace-allocation-amount"><strong>{money(row.amount_eur)}</strong><span>{percentage.toFixed(1)}%</span></div>
      </div>;
    })}
    <details className="workspace-allocation-note"><summary>How allocation is estimated</summary>
      <p>Illustrative split using historical EUR round totals divided by participant counts. Actual individual checks and willingness to invest are unconfirmed. Percentages describe the open allocation, not investment likelihood.</p>
    </details>
  </div>;
}
