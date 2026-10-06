// @vitest-environment happy-dom
import { chromeSnapshotForRoute, expectValidBaseAddon, TEST_SCHEMAS } from "@angee/app/testing";
import { composeAddons } from "@angee/app";
import { MenuTree, resolveMenuRouteTargets } from "@angee/ui/chrome/menu-tree";
import { createRouteHref } from "@angee/ui/runtime";
import { expect, test } from "vitest";
import decisions from "@angee/decisions";
import { decisionResourceFixture } from "@angee/decisions/testing";
import addon from "./index";
import { runResourceFixture, workflowResourceFixture } from "./testing";
import { triggerEventResourceFixture, triggerResourceFixture } from "./trigger-testing";

// Workflows includes decisions' menu and joins its origin container; the composed runtime supplies the dependency.
const decisionsMenu = { ...decisions };

test("flattens Decisions into Workflows' top bar while the rail keeps one app", () => {
  const { menuComposition } = composeAddons([decisionsMenu, { ...addon, dependsOn: ["decisions"] }], { canonicalModelLabel: (model) => model });
  expect(menuComposition.diagnostics).toEqual([]);
  const tree = MenuTree.from(resolveMenuRouteTargets(menuComposition.navigation, createRouteHref([...(decisions.routes ?? []), ...(addon.routes ?? [])])));
  expect(tree.roots.map((node) => node.id)).toEqual(["workflows"]);
  const workflows = tree.byId.get("workflows")!;
  expect(workflows.appChildren()).toEqual([]);
  expect(workflows.menuItems().map((node) => node.label)).toEqual(["Runs", "Decisions", "Studio"]);
  expect(tree.byId.get("decisions.queue")?.children?.map((node) => node.label)).toEqual(["Inbox", "Waiting on me", "All decisions"]);
  expect(tree.byId.get("workflows.studio")?.children?.map((node) => node.id)).toEqual(["workflows.catalogue", "workflows.triggers"]);
});

test("registers read-only workflow and run pages with additive record and decision context", () => {
  expectValidBaseAddon(addon);
  expect(addon.routes?.map((route) => route.name)).toContain("workflows.runs.record");
  expect(Object.keys(addon.containers?.["decisions#origin"] ?? {})).toEqual(["workflows.run"]);
  expect(Object.keys(addon.containers?.["record#aside"] ?? {})).toEqual(["workflows.timeline"]);
  expect(Object.keys(addon.containers?.["form#actions-menu"] ?? {})).toEqual(["workflows.run-workflow"]);
});

test("trigger record breadcrumbs inherit the declared Triggers collection label", async () => {
  const chrome = await chromeSnapshotForRoute({ addons: [decisionsMenu, { ...addon, dependsOn: ["decisions"] }], path: "/workflows/triggers/wft_review", schemas: {
    ...TEST_SCHEMAS,
    console: { ...TEST_SCHEMAS.console, metadata: { angee: { resources: [
      decisionResourceFixture, workflowResourceFixture, runResourceFixture, triggerResourceFixture, triggerEventResourceFixture,
    ] } } },
  } });
  expect(chrome.breadcrumbs).toContainEqual({ label: "Triggers", to: "/workflows/triggers" });
  expect(chrome.breadcrumbs.map(({ label }) => label)).not.toContain("trigger.trigger");
});
