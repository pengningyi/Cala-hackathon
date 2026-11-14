import { expect, test } from "@playwright/test";
import { activeRunStage } from "../lib/run-progress";

const events = (...steps: string[]) => steps.map(step => ({ step }));

test("interleaved discovery responses cannot rewind portfolio or scoring progress", () => {
  const discovery = events("confirmation", "trust", "lead_graph", "profiles", "founder_network", "profiles");
  expect(activeRunStage(discovery)).toBe(0);
  const portfolio = [...discovery, ...events("company_context", "trust", "founder_network")];
  expect(activeRunStage(portfolio)).toBe(1);
  const scoring = [...portfolio, ...events("scoring", "loops", "trust", "profiles")];
  expect(activeRunStage(scoring)).toBe(2);
  expect(activeRunStage([...scoring, ...events("finalizing", "company_context")])).toBe(3);
});

test("terminal or unknown events retain the reached stage, including early failures", () => {
  expect(activeRunStage(events("confirmation", "lead_graph", "complete"))).toBe(0);
  expect(activeRunStage(events("assessments", "retry", "unknown", "complete"))).toBe(1);
  expect(activeRunStage(events("scoring", "complete"))).toBe(2);
  expect(activeRunStage(events("finalizing", "complete"))).toBe(3);
  expect(activeRunStage([])).toBe(0);
});
