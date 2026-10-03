// @vitest-environment happy-dom
import { chromeSnapshotForRoute, expectValidBaseAddon, TEST_SCHEMAS } from "@angee/app/testing";
import { expect, test } from "vitest";
import decisions from "@angee/decisions";
import addon from "./index";
import { runResourceFixture, workflowResourceFixture } from "./testing";
import { triggerEventResourceFixture, triggerResourceFixture } from "./trigger-testing";

// Workflows includes decisions' menu and joins its origin container; the composed runtime supplies the dependency.
const decisionsMenu = { id: "decisions", menus: [{ id: "decisions", label: "Decisions" }], containers: decisions.containers };

test("registers read-only workflow and run pages with additive record and decision context", () => {
  expectValidBaseAddon(addon);
  expect(addon.routes?.map((route) => route.name)).toContain("workflows.runs.record");
  expect(Object.keys(addon.containers?.["decisions#origin"] ?? {})).toEqual(["workflows.run"]);
  expect(Object.keys(addon.containers?.["record#aside"] ?? {})).toEqual(["workflows.runs"]);
});

test("trigger record breadcrumbs inherit the declared Triggers collection label", async () => {
  const chrome = await chromeSnapshotForRoute({ addons: [decisionsMenu, { ...addon, dependsOn: ["decisions"] }], path: "/workflows/triggers/wft_review", schemas: {
    ...TEST_SCHEMAS,
    console: { ...TEST_SCHEMAS.console, metadata: { angee: { resources: [
      workflowResourceFixture, runResourceFixture, triggerResourceFixture, triggerEventResourceFixture,
    ] } } },
  } });
  expect(chrome.breadcrumbs).toContainEqual({ label: "Triggers", to: "/workflows/triggers" });
  expect(chrome.breadcrumbs.map(({ label }) => label)).not.toContain("trigger.trigger");
});
