import { expect, test, type Page } from "@playwright/test";
import type { Entity, Source } from "../lib/types";

const source: Source = {
  url: "https://synthetic.example.test/fund-profile", label: "Synthetic fund profile",
  published_at: "2026-11-13", retrieved_at: "2026-11-14T09:00:00Z",
};

function entity(id: string, overrides: Partial<Entity> = {}): Entity {
  return {
    id, name: `Synthetic ${id}`, kind: "unknown", country: null, website: null,
    description: "Synthetic description from Cala.", aliases: [], related_entity_ids: [], sources: [],
    ...overrides,
  };
}

async function resolveWith(page: Page, matches: Entity[]) {
  await page.route("**/api/health", route => route.fulfill({ json: { status: "healthy", cala_configured: true, model_provider: "disabled" } }));
  await page.route("**/api/onboarding/matches", route => {
    return route.fulfill({ json: {
      id: "synthetic-matches", lead_matches: matches, startup_matches: [entity("startup", { name: "Cala AI" })],
      resolver_ms: 25, created_at: "2026-11-14T09:00:00Z", expires_at: new Date(Date.now() + 3600000).toISOString(), notices: [],
    } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Find matching companies", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Choose your lead", exact: true })).toBeVisible();
}

test("full metadata distinguishes headquarters, entity type and investor category with dated field evidence", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await resolveWith(page, [entity("lead", {
    country: "United States", entity_type: "Company", website: "https://synthetic.example.test/",
    headquarters_address: "1 Example Road, Test City, United States", registered_address: "2 Registered Road, Mauritius",
    country_basis: "headquarters_address", country_sources: [source], metadata_status: "retrieved",
    metadata_retrieved_at: source.retrieved_at,
    metadata: [
      { name: "legal_name", value: "Synthetic Lead Management LLC", sources: [source] },
      { name: "aliases", value: ["Synthetic Fund", "Synthetic Ventures"], sources: [source] },
      { name: "operational_status", value: { active: true, observed_rounds: 0 }, sources: [source] },
      { name: "description", value: "<script>window.metadataInjected=true</script>", sources: [] },
    ],
  })]);
  const match = page.locator(".confirmation-section").filter({ has: page.getByRole("heading", { name: "Choose your lead", exact: true }) }).locator(".entity-option").first();
  await expect(match.locator(".entity-choice")).toContainText("Headquarters: United States");
  await expect(match.locator(".entity-choice")).toContainText("Entity type: Company");
  await expect(match.locator(".entity-choice")).not.toContainText("Venture capital");
  await expect(match.locator(".entity-choice")).not.toContainText("Type unknown");
  await expect(match.locator(".entity-choice")).toHaveAttribute("aria-pressed", "false");
  const details = match.locator(".entity-metadata");
  await details.locator("summary").press("Enter");
  await expect(details).toHaveAttribute("open", "");
  await expect(match.locator(".entity-choice")).toHaveAttribute("aria-pressed", "false");
  await expect(details).toContainText("Headquarters address");
  await expect(details).toContainText("2 Registered Road, Mauritius");
  await expect(details).toContainText("Investor categoryUnconfirmed");
  await expect(details).toContainText("Synthetic Lead Management LLC");
  await expect(details).toContainText("Synthetic Ventures");
  await expect(details).toContainText("ActiveYes");
  await expect(details).toContainText("Observed rounds0");
  await expect(details).toContainText("Published 13 Nov 2026 · Retrieved 14 Nov 2026");
  await expect(details.getByRole("link", { name: "Synthetic fund profile", exact: true }).first()).toHaveAttribute("href", source.url);
  await expect(details.getByRole("link", { name: "synthetic.example.test", exact: true })).toHaveAttribute("href", "https://synthetic.example.test/");
  await expect(details).toContainText("<script>window.metadataInjected=true</script>");
  expect(await page.evaluate(() => "metadataInjected" in window)).toBeFalsy();
  await expect(details).toContainText("No source link returned for this field.");
  await match.locator(".entity-choice").click();
  await expect(match.locator(".entity-choice")).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: ".qa/metadata-confirmation-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: ".qa/metadata-confirmation-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("supported investor category and unavailable metadata preserve selectable matches", async ({ page }) => {
  await resolveWith(page, [entity("classified", {
    kind: "vc", entity_type: "Organization", country: "Mauritius", kind_sources: [source],
    metadata_status: "retrieved", metadata: [],
  }), entity("unavailable", { entity_type: "Company", metadata_status: "unavailable" })]);
  const classified = page.locator(".confirmation-section").filter({ has: page.getByRole("heading", { name: "Choose your lead", exact: true }) }).locator(".entity-option").first();
  await expect(classified.locator(".entity-choice")).toContainText("Entity type: Organization");
  await expect(classified.locator(".entity-choice")).toContainText("Investor category: Venture capital");
  const fallback = page.locator(".confirmation-section").filter({ has: page.getByRole("heading", { name: "Choose your lead", exact: true }) }).locator(".entity-option").nth(1);
  await expect(fallback).toContainText("Additional Cala details could not be retrieved");
  await expect(fallback).toContainText("Synthetic description from Cala.");
  await expect(fallback.locator(".entity-choice")).toContainText("Entity type: Company");
  await fallback.locator(".entity-choice").click();
  await expect(fallback.locator(".entity-choice")).toHaveAttribute("aria-pressed", "true");
  await expect(classified.locator(".entity-choice")).toHaveAttribute("aria-pressed", "false");
});
