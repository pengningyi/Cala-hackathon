import type { ProgressEvent } from "./types";

export const RUN_STAGES = [
  "Discover & profile investors",
  "Review portfolio evidence",
  "Score fit & connections",
  "Prepare shortlist & requests",
] as const;

const STAGE_BY_STEP: Readonly<Record<string, number>> = {
  confirmation: 0, lead_graph: 0, founder_network: 0, profiles: 0, trust: 0,
  assessments: 1, company_context: 1,
  scoring: 2, loops: 2,
  finalizing: 3,
};

export function activeRunStage(events: readonly Pick<ProgressEvent, "step">[]): number {
  // Discovery and profile lookups overlap. A late response must not rewind the bar.
  // A terminal event can also mean an early failure, so it does not advance stages.
  return events.reduce((stage, event) => Math.max(stage, STAGE_BY_STEP[event.step] ?? stage), 0);
}
