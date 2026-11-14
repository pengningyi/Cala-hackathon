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

async function resolveWith(page: Page, matches: Entity[], startup: Partial<Entity> = {}) {
  await page.route("**/api/health", route => route.fulfill({ json: { status: "healthy", cala_configured: true, model_provider: "disabled" } }));
  await page.route("**/api/onboarding/matches", route => {
    return route.fulfill({ json: {
      id: "synthetic-matches", lead_matches: matches, startup_matches: [entity("startup", { name: "Cala AI", ...startup })],
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
    headquarters_address: "1 Example Road, Test City, United States", office_address: "10 Office Street, London, United Kingdom",
    registered_address: "2 Registered Road, Mauritius",
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
  await expect(details).toContainText("Office address");
  await expect(details).toContainText("10 Office Street, London, United Kingdom");
  await expect(match.locator(".entity-choice")).not.toContainText("office address fallback");
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

test("office-country fallback stays distinct from headquarters and registration evidence", async ({ page }) => {
  const officeSource: Source = { ...source, url: "https://synthetic.example.test/office", label: "Office address record" };
  const registrationSource: Source = { ...source, url: "https://synthetic.example.test/registration", label: "Registration address record" };
  await resolveWith(page, [entity("office-fallback", {
    country: "United Kingdom", country_basis: "office_address", country_sources: [officeSource],
    office_address: "10 Office Street, London, United Kingdom", registered_address: "2 Registered Road, Mauritius",
    metadata_status: "retrieved", metadata: [{ name: "registered_address", value: "2 Registered Road, Mauritius", sources: [registrationSource] }],
  })]);
  const match = page.locator(".confirmation-section").filter({ has: page.getByRole("heading", { name: "Choose your lead", exact: true }) }).locator(".entity-option").first();
  await expect(match.locator(".entity-choice")).toContainText("Country: United Kingdom (office address fallback)");
  await expect(match.locator(".entity-choice")).not.toContainText("Headquarters: United Kingdom");
  const details = match.locator(".entity-metadata");
  await details.locator("summary").click();
  const country = details.locator(".entity-metadata-overview > div").filter({ has: page.locator("dt").filter({ hasText: /^Country$/ }) });
  await expect(country).toContainText("United Kingdom");
  await expect(country).toContainText("Office address fallback; headquarters unconfirmed.");
  await expect(country.getByRole("link", { name: "Office address record", exact: true })).toHaveAttribute("href", officeSource.url);
  await expect(country.getByRole("link", { name: "Registration address record", exact: true })).toHaveCount(0);
  await expect(details).toContainText("Office address10 Office Street, London, United Kingdom");
  await expect(details).toContainText("Registered address2 Registered Road, Mauritius");
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: ".qa/country-office-fallback-mobile.png", fullPage: true });
});

for (const basis of ["registered_address", "registration_country_property", "registration_relationship"]) {
  test(`registration-country fallback identifies its basis (${basis})`, async ({ page }) => {
    const registrationSource: Source = { ...source, url: "https://synthetic.example.test/registration", label: "Registration address record" };
    await resolveWith(page, [entity("registration-fallback", {
      country: "Mauritius", country_basis: basis, country_sources: [registrationSource],
      office_address: null, registered_address: "2 Registered Road, Mauritius", metadata_status: "retrieved",
    })]);
    const match = page.locator(".confirmation-section").filter({ has: page.getByRole("heading", { name: "Choose your lead", exact: true }) }).locator(".entity-option").first();
    await expect(match.locator(".entity-choice")).toContainText("Country: Mauritius (registration address fallback)");
    await expect(match.locator(".entity-choice")).not.toContainText("Headquarters: Mauritius");
    const details = match.locator(".entity-metadata");
    await details.locator("summary").click();
    await expect(details).toContainText("Registration address fallback; headquarters unconfirmed.");
    await expect(details.getByRole("link", { name: "Registration address record", exact: true })).toHaveAttribute("href", registrationSource.url);
    await expect(details).toContainText("Registered address2 Registered Road, Mauritius");
    if (basis === "registered_address") {
      await page.setViewportSize({ width: 390, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
      await page.screenshot({ path: ".qa/country-registration-fallback-mobile.png", fullPage: true });
    }
  });
}

test("office text without a confirmed country remains unknown in the identity summary", async ({ page }) => {
  await resolveWith(page, [entity("unknown-country", {
    country: null, country_basis: null, office_address: "Unverified office address mentioning London, United Kingdom",
    registered_address: "A registration address mentioning Mauritius", metadata_status: "retrieved",
  })]);
  const match = page.locator(".confirmation-section").filter({ has: page.getByRole("heading", { name: "Choose your lead", exact: true }) }).locator(".entity-option").first();
  await expect(match.locator(".entity-choice")).toContainText("Headquarters country unknown");
  await expect(match.locator(".entity-choice")).not.toContainText("Country: United Kingdom");
  await expect(match.locator(".entity-choice")).not.toContainText("Country: Mauritius");
  await match.locator(".entity-metadata summary").click();
  await expect(match.locator(".entity-metadata")).toContainText("Unverified office address mentioning London, United Kingdom");
});

for (const scenario of [
  { basis: "office_address", country: "United Kingdom", kind: "office", address: "10 Office Street, London, United Kingdom" },
  { basis: "registered_address", country: "Mauritius", kind: "registration", address: "2 Registered Road, Mauritius" },
]) {
  test(`confirmed startup ${scenario.kind} fallback leaves headquarters empty and editable`, async ({ page }) => {
    const countrySource: Source = { ...source, url: `https://synthetic.example.test/startup-${scenario.kind}`,
      label: `Startup ${scenario.kind} address record` };
    const startup = entity("startup", { name: "Cala AI", country: scenario.country, country_basis: scenario.basis,
      country_sources: [countrySource], metadata_status: "retrieved",
      office_address: scenario.basis === "office_address" ? scenario.address : null,
      registered_address: scenario.basis === "registered_address" ? scenario.address : null });
    const lead = entity("lead");
    let confirmedIds: { startup_id: string; lead_id: string } | null = null;
    await page.route("**/api/onboarding/matches/synthetic-matches/confirm", route => {
      confirmedIds = route.request().postDataJSON();
      return route.fulfill({ json: { resolution_id: "confirmed-startup", startup, lead,
        prefill: { company_name: startup.name, website: "", sector: "Software", country: "" },
        prefill_sources: { company_name: [], website: [], sector: [], country: [] },
        expires_at: new Date(Date.now() + 3600000).toISOString(), notices: [] } });
    });
    await resolveWith(page, [lead], startup);
    const startupOption = page.locator(".entity-choice").filter({ hasText: startup.name });
    await expect(startupOption).toContainText(`Country: ${scenario.country} (${scenario.kind} address fallback)`);
    const metadata = page.locator(".entity-option").filter({ has: startupOption }).locator(".entity-metadata");
    await metadata.locator("summary").first().click();
    await expect(metadata).toContainText(`${scenario.kind === "office" ? "Office" : "Registration"} address fallback; headquarters unconfirmed.`);
    await expect(metadata.getByRole("link", { name: countrySource.label, exact: true })).toHaveAttribute("href", countrySource.url);
    await startupOption.click();
    await page.locator(".entity-choice").filter({ hasText: lead.name }).click();
    await page.getByRole("button", { name: "Confirm companies", exact: true }).click();
    const country = page.getByPlaceholder("Country, if known", { exact: true });
    await expect(country).toHaveValue("");
    await expect(country).toBeEditable();
    expect(confirmedIds).toEqual({ startup_id: startup.id, lead_id: lead.id });
    await expect(page.locator(".prefill-evidence")).toHaveCount(0);
    await expect(page.getByRole("link", { name: countrySource.label, exact: true })).toHaveCount(0);
    const reviewed = page.getByLabel("I have reviewed my round details.", { exact: true });
    await reviewed.check();
    await country.fill("France");
    await expect(country).toHaveValue("France");
    await expect(reviewed).not.toBeChecked();
    await expect(page.getByRole("link", { name: countrySource.label, exact: true })).toHaveCount(0);
  });
}
