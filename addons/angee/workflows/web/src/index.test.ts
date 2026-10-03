// @vitest-environment happy-dom
import { chromeSnapshotForRoute, expectValidBaseAddon, TEST_SCHEMAS } from "@angee/app/testing";
import { expect, test } from "vitest";
import { DECISION_ORIGIN_SLOT } from "@angee/decisions";
import decisions from "@angee/decisions";
import addon from "./index";
import { runResourceFixture, workflowResourceFixture } from "./testing";
import { triggerEventResourceFixture, triggerResourceFixture } from "./trigger-testing";

test("registers read-only workflow and run pages with additive record and decision context", () => {
  expectValidBaseAddon(addon);
  expect(addon.routes?.map((route) => route.name)).toContain("workflows.runs.record");
  expect(addon.slots?.some((slot) => slot.slot === DECISION_ORIGIN_SLOT)).toBe(true);
  expect(addon.chatter?.map((entry) => entry.id)).toEqual(["workflows"]);
});

test("trigger record breadcrumbs inherit the declared Triggers collection label", async () => {
  const chrome = await chromeSnapshotForRoute({ addons: [decisions, { ...addon, dependsOn: [decisions.id] }], path: "/workflows/triggers/wft_review", schemas: {
    ...TEST_SCHEMAS,
    console: { ...TEST_SCHEMAS.console, metadata: { angee: { resources: [
      workflowResourceFixture, runResourceFixture, triggerResourceFixture, triggerEventResourceFixture,
    ] } } },
  } });
  expect(chrome.breadcrumbs).toContainEqual({ label: "Triggers", to: "/workflows/triggers" });
  expect(chrome.breadcrumbs.map(({ label }) => label)).not.toContain("trigger.trigger");
});
