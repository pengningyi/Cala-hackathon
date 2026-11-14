import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs/promises";

async function fillProfile(page: Page, fullyCommitted = false) {
  await page.getByLabel("Company name", { exact: true }).fill("RoundCloser browser demo");
  await page.getByLabel(/^Company website/).fill("");
  await page.getByLabel(/^Also look up my company in Cala/).uncheck();
  await page.getByLabel("What does your product do?", { exact: true }).fill("AI software that helps marketing teams automate campaign reporting.");
  await page.getByLabel("Sector", { exact: true }).fill("Marketing software");
  await page.getByLabel("Headquarters country", { exact: true }).fill("United Kingdom");
  await page.getByLabel("Who is your target customer?", { exact: true }).fill("Marketing teams at small businesses");
  await page.getByLabel("What problem do you solve?", { exact: true }).fill("Manual campaign reporting and repetitive marketing work");
  await page.getByLabel(/^Round target/).fill("2000000");
  await page.getByLabel(/^Firm commitments/).fill(fullyCommitted ? "2000000" : "1200000");
  await page.getByLabel(/^Conditional interest/).fill("300000");
  await page.getByLabel("Lead investor name", { exact: true }).fill("Seedcamp");
  await page.getByLabel("Lead commitment status", { exact: true }).selectOption("conditional");
}

test("responsive Cala test profile has no invented results or horizontal overflow", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Find who comes next");
  await expect(page.getByLabel("Company name", { exact: true })).toHaveValue("Cala");
  await expect(page.getByLabel(/^Company website/)).toHaveValue("https://cala.ai");
  await expect(page.getByRole("note")).toContainText("hypothetical");
  await expect(page.locator(".candidate-card")).toHaveCount(0);
  await fs.mkdir(".qa", { recursive: true });
  await page.screenshot({ path: ".qa/form-desktop.png", fullPage: true });
  for (const width of [768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByLabel("Company name", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await page.screenshot({ path: `.qa/form-${width}.png`, fullPage: true });
  }
  expect(errors).toEqual([]);
});

test("filled allocation stops discovery through the real API", async ({ page }) => {
  await page.goto("/");
  await fillProfile(page, true);
  await page.getByRole("button", { name: "Find my lead", exact: true }).click();
  await expect(page.getByRole("heading", { name: "No open allocation", exact: true })).toBeVisible();
  await page.getByLabel("I confirm this is the correct lead and startup profile.").check();
  await page.getByRole("button", { name: "Confirm round summary", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("no open allocation");
  await expect(page.locator(".candidate-card")).toHaveCount(0);
  await expect(page.locator(".result-meta")).toContainText("0 Cala calls");
});

test("real Cala search streams, copies follow-up drafts and reruns a do-not-contact exclusion", async ({ page }) => {
  const errors: string[] = [];
  const streamed: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => { if (response.url().includes("/events")) streamed.push(response.url()); });
  await page.goto("/");
  await fillProfile(page);
  await page.getByRole("button", { name: "Add an investor status" }).click();
  await page.getByLabel("Investor 1 name", { exact: true }).fill("Accel");
  await page.getByLabel("Investor 1 status", { exact: true }).selectOption("already_approached");
  await page.getByRole("button", { name: "Find my lead", exact: true }).click();
  const lead = page.locator(".entity-option").filter({ hasText: "aa87dde8-7dc3-4fc0-957f-143da6ddc0b7" });
  await expect(lead).toBeVisible();
  await lead.getByRole("button").click();
  await page.getByLabel("I confirm this is the correct lead and startup profile.").check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await expect(page.getByText("Search in progress", { exact: true })).toBeVisible();
  await expect(page.locator(".trace-event").nth(1)).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".shortlist-heading")).toBeVisible({ timeout: 95_000 });
  const rows = page.locator(".workspace-investor-row");
  expect(await rows.count()).toBeGreaterThan(0);
  expect(await rows.count()).toBeLessThanOrEqual(5);
  await expect(page.locator(".workspace-hero")).toContainText("Provisional lead");
  await expect(page.getByRole("heading", { name: "Your round assistant", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Founder and investor knowledge graph" })).toBeVisible();
  const chatBox = await page.locator(".workspace-chat-panel").boundingBox();
  const listBox = await page.locator(".workspace-shortlist").boundingBox();
  const graphBox = await page.locator(".workspace-graph-panel").boundingBox();
  expect(graphBox!.x).toBeGreaterThan(chatBox!.x + chatBox!.width);
  expect(listBox!.y).toBeGreaterThanOrEqual(chatBox!.y + chatBox!.height);
  expect(graphBox!.height).toBeGreaterThan(chatBox!.height);

  await page.getByRole("button", { name: "Select Accel", exact: true }).click();
  await expect(page.locator(".rc-chat-selection")).toContainText("Accel");
  await expect(page.getByRole("button", { name: "Shortlisted investor: Accel", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Compare the top two", exact: true }).click();
  await expect(page.locator(".rc-chat-message-assistant")).toHaveCount(2);
  await expect(page.locator(".rc-chat-message-assistant").last()).toContainText("Comparison of recorded cards");
  await expect(page.locator(".rc-chat-message-assistant").last().locator(".rc-chat-citations")).toBeVisible();
  await expect(page.locator(".rc-chat-answer-meta")).toContainText("From retrieved evidence");

  const graphTransform = await page.locator(".kg-canvas > g").getAttribute("transform");
  await page.getByRole("button", { name: "Zoom graph in", exact: true }).click();
  expect(await page.locator(".kg-canvas > g").getAttribute("transform")).not.toBe(graphTransform);
  await page.getByRole("button", { name: "Reset graph view", exact: true }).click();
  await page.getByRole("button", { name: /^Identified funding round:/ }).first().click();
  await expect(page.locator(".kg-inspector")).toContainText("Recipient:");
  expect(await page.locator(".kg-inspector .kg-source-list a").count()).toBeGreaterThan(0);
  await page.screenshot({ path: ".qa/results-workspace-desktop.png", fullPage: true });
  for (const width of [768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await page.screenshot({ path: `.qa/results-workspace-${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "View details for Accel", exact: true }).click();
  const contacted = page.getByRole("dialog").locator(".candidate-card");
  await expect(contacted).toHaveCount(1);
  await contacted.getByRole("button", { name: "Copy follow-up request", exact: true }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("existing conversation");
  expect(copied).toContain("provisional");
  await expect(contacted.locator(".draft-box pre")).toContainText("Cala shows");
  const sourceLinks = await contacted.locator(".source-item a").evaluateAll(elements => elements.map(element => (element as HTMLAnchorElement).href));
  expect(sourceLinks.length).toBeGreaterThan(0);
  expect(sourceLinks.every(url => url.startsWith("http"))).toBeTruthy();
  await contacted.getByRole("button", { name: /Do not contact/ }).click();
  await expect(page.locator(".shortlist-heading")).toBeVisible({ timeout: 95_000 });
  await expect(page.getByRole("button", { name: "Select Accel", exact: true })).toHaveCount(0);
  await expect(page.locator(".exclusion-row").filter({ hasText: "Accel" })).toContainText("do-not-contact");
  await expect(page.locator(".rc-chat-message-assistant")).toHaveCount(1);
  expect(streamed.length).toBeGreaterThanOrEqual(2);
  expect(errors).toEqual([]);
});
