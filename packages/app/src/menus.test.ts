import { describe, expect, test } from "vitest";

import { compileMenus, type CompiledMenuItem, type MenuLayer } from "./menus";
import { DEPLOYMENT_LAYER_ID } from "./shell";

const projects: MenuLayer = {
  id: "projects",
  menus: [{ id: "projects", label: "Projects", children: [
    { id: "projects.my-work", route: "projects.my-work" },
    { id: "projects.board", route: "projects.board" },
    { id: "projects.tasks", route: "projects.tasks" },
  ] }],
};
const work: MenuLayer = {
  id: "work",
  dependsOn: ["projects"],
  menus: [{ id: "work", label: "Work", children: [
    { id: "work.triage-hub", route: "work.triage-hub" },
    { id: "work.cycles-hub", route: "work.cycles-hub" },
  ] }],
};
const pm: MenuLayer = {
  id: "pm",
  dependsOn: ["projects", "work"],
  menus: {
    pm: { label: "Work", sequence: 1, include: [{ id: "projects", flatten: true }, { id: "work", flatten: true }] },
    "pm.inbox": { parent: "pm", route: "messaging.messages", sequence: 10 },
    "projects.my-work": { sequence: 20 },
    "work.triage-hub": { sequence: 30 },
    "projects.board": { remove: true },
  },
};

type Tree = readonly { id: string; hidden?: boolean; children?: Tree }[];
const ids = (items: Tree): unknown =>
  items.map((item) => (item.children?.length ? { [item.id]: ids(item.children) } : item.id));
/** What the rail renders: the navigation tree without hidden nodes. */
const rail = (items: Tree): unknown => ids(strip(items));
const strip = (items: Tree): Tree => items.filter((item) => !item.hidden)
  .map((item) => ({ ...item, ...(item.children ? { children: strip(item.children) } : {}) }));

describe("compileMenus", () => {
  test("Nexus includes emit app identity in both trees; legacy parentId children do not", () => {
    const result = compileMenus([
      { id: "messaging", menus: { messaging: { route: "messaging.messages" } } },
      { id: "parties", menus: { parties: { route: "parties.people" } } },
      { id: "nexus", dependsOn: ["messaging", "parties"], menus: {
        nexus: { route: "nexus.inbox", include: ["messaging", "parties"] },
      } },
      { id: "legacy", menus: [{ id: "legacy.page", parentId: "nexus", route: "legacy.page" }] },
    ]);
    for (const items of [result.navigation, result.logical]) {
      expect(items[0]?.children?.map((item) => [item.id, item.app])).toEqual([
        ["legacy.page", undefined], ["messaging", true], ["parties", true],
      ]);
    }
    // A removed included app is recorded as one, so developer mode shows it in the rail.
    const removedApp = compileMenus([
      { id: "messaging", menus: { messaging: { route: "messaging.messages" } } },
      { id: "parties", menus: { parties: { route: "parties.people" } } },
      { id: "nexus", dependsOn: ["messaging", "parties"], menus: { nexus: { route: "nexus.inbox", include: ["messaging", "parties"] } } },
      { id: "product", dependsOn: ["nexus"], menus: { parties: { remove: true } } },
    ]);
    expect(removedApp.removed).toEqual([{ id: "parties", route: "parties.people", by: "product", parent: "nexus", app: true }]);
    expect(() => compileMenus([{ id: "desk", menus: { desk: { app: true } } } as unknown as MenuLayer]))
      .toThrow(/unknown key "app"/);
    // Only a Settings root may be personal: one every perspective keeps.
    expect(compileMenus([{ id: "look", menus: { look: { group: "platform", personal: true } } }]).navigation[0]?.personal).toBe(true);
    expect(() => compileMenus([{ id: "look", menus: { look: { personal: true } } }])).toThrow(/is personal, which only a Settings root/);
    expect(() => compileMenus([{ id: "look", menus: { look: {}, "look.theme": { parent: "look", group: "platform", personal: true } } }]))
      .toThrow(/is personal, which only a Settings root/);
    expect(() => compileMenus([{ id: "desk", menus: [{ id: "desk", app: true }] } as unknown as MenuLayer]))
      .toThrow(/app identity is compiler-emitted/);
  });

  test("legacy arrays compile to the same tree", () => {
    const { logical, navigation, removed } = compileMenus([projects, work]);
    expect(ids(logical)).toEqual([
      { projects: ["projects.my-work", "projects.board", "projects.tasks"] },
      { work: ["work.triage-hub", "work.cycles-hub"] },
    ]);
    expect(navigation).toEqual(logical);
    expect(removed).toEqual([]);
  });

  test("an aggregator includes apps, flattens them in navigation and keeps them in the logical tree", () => {
    const { logical, navigation, removed } = compileMenus([projects, work, pm]);
    expect(ids(logical)).toEqual([{ pm: [
      "pm.inbox",
      { projects: ["projects.my-work", "projects.tasks"] },
      { work: ["work.triage-hub", "work.cycles-hub"] },
    ] }]);
    expect((logical[0]!.children as CompiledMenuItem[]).find((item) => item.id === "projects")?.flatten).toBe(true);
    expect((logical[0]!.children as CompiledMenuItem[]).find((item) => item.id === "projects")?.app).toBeUndefined();
    expect(navigation[0]?.children?.some((item) => item.id === "projects" || item.id === "work")).toBe(false);
    expect(rail(navigation)).toEqual([{ pm: [
      "pm.inbox", "projects.my-work", "work.triage-hub", "projects.tasks", "work.cycles-hub",
    ] }]);
    expect(removed).toEqual([{ id: "projects.board", route: "projects.board", by: "pm", parent: "pm" }]);
  });

  test("removal takes the subtree; hide keeps the node in the logical tree only", () => {
    const product: MenuLayer = { id: "product", dependsOn: ["pm", "projects", "work"], menus: {
      work: { remove: true }, "projects.tasks": { hide: true },
    } };
    const { logical, navigation, removed } = compileMenus([projects, work, pm, product]);
    expect(removed.map((node) => node.id)).toEqual(["projects.board", "work", "work.triage-hub", "work.cycles-hub"]);
    expect(ids(logical)).toEqual([{ pm: ["pm.inbox", { projects: ["projects.my-work", "projects.tasks"] }] }]);
    expect(rail(navigation)).toEqual([{ pm: ["pm.inbox", "projects.my-work"] }]);
  });

  test("a dependent may un-hide; only narrows and never filters its own author's dependents", () => {
    const narrow: MenuLayer = { id: "narrow", dependsOn: ["pm"], menus: {
      pm: { only: ["pm.inbox", "projects", "work", "projects.tasks"] },
      "narrow.page": { parent: "pm", route: "narrow.page" },
    } };
    const top: MenuLayer = { id: "top", dependsOn: ["narrow"], menus: {
      pm: { only: ["pm.inbox", "projects"] },
      "top.page": { parent: "pm", route: "top.page" },
      "projects.tasks": { hide: true },
    } };
    const unhide: MenuLayer = { id: "unhide", dependsOn: ["top"], menus: { "projects.tasks": { hide: false } } };
    // narrow.page passes narrow's own only but not top's; work's items fail top's.
    expect(rail(compileMenus([projects, work, pm, narrow, top]).navigation)).toEqual([
      { pm: ["pm.inbox", "projects.my-work", "top.page"] },
    ]);
    expect(rail(compileMenus([projects, work, pm, narrow, top, unhide]).navigation)).toEqual([
      { pm: ["pm.inbox", "projects.my-work", "projects.tasks", "top.page"] },
    ]);
    // A deployment `only` narrows like any layer's; with `force` it stands in for every
    // layer's (G-14), bringing back work's items narrow and top left out and leaving top's page.
    const deployment = (entry: Record<string, unknown>): MenuLayer =>
      ({ id: DEPLOYMENT_LAYER_ID, dependsOn: ["projects", "work", "pm", "narrow", "top"], menus: { pm: entry } as MenuLayer["menus"] });
    expect(rail(compileMenus([projects, work, pm, narrow, top, deployment({ only: ["pm.inbox", "work", "narrow.page"] })]).navigation))
      .toEqual([{ pm: ["pm.inbox"] }]);
    const forced = compileMenus([projects, work, pm, narrow, top, deployment({ only: ["pm.inbox", "work", "narrow.page"], force: true })]);
    expect(rail(forced.navigation)).toEqual([{ pm: ["pm.inbox", "work.triage-hub", "work.cycles-hub", "narrow.page"] }]);
    expect(forced.hidden.find((entry) => entry.id === "top.page")).toEqual({ id: "top.page", by: DEPLOYMENT_LAYER_ID, reason: "only" });
    // Only the deployment forces, and only an `only`.
    expect(() => compileMenus([projects, work, pm, { ...narrow, menus: { pm: { only: ["pm.inbox"], force: true } } }]))
      .toThrow(/only the deployment forces/);
    expect(() => compileMenus([projects, work, pm, narrow, top, deployment({ force: true })])).toThrow(/only the deployment forces/);
  });

  test("the gate: only nodes of addons a layer depends on can be altered", () => {
    expect(() => compileMenus([projects, work, { id: "stranger", menus: { "work.cycles-hub": { hide: true } } }]))
      .toThrow(/"stranger" alters menu item "work.cycles-hub" of "work", which it does not depend on/);
    expect(() => compileMenus([projects, { id: "x", dependsOn: ["projects"], menus: { "projects.ghost": { hide: true } } }]))
      .toThrow(/alters unknown menu item "projects.ghost"/);
  });

  test("a dependent overrides its dependency; unrelated layers setting one field collide; the deployment wins", () => {
    const left: MenuLayer = { id: "left", dependsOn: ["projects"], menus: { projects: { label: "Left" } } };
    const right: MenuLayer = { id: "right", dependsOn: ["projects"], menus: { projects: { label: "Right" } } };
    const over: MenuLayer = { id: "over", dependsOn: ["left"], menus: { projects: { label: "Over" } } };
    expect(compileMenus([projects, left, over]).logical[0]!.label).toBe("Over");
    expect(compileMenus([projects, over, left]).logical[0]!.label).toBe("Over");
    expect(() => compileMenus([projects, left, right])).toThrow(/Unrelated addons "left" and "right" both set menu "projects" label/);
    const deployment: MenuLayer = { id: DEPLOYMENT_LAYER_ID, dependsOn: ["projects", "left"], menus: { projects: { label: "Pinned" } } };
    expect(compileMenus([projects, left, deployment]).logical[0]!.label).toBe("Pinned");
    expect(compileMenus([projects, left, deployment]).provenance.projects).toEqual({ label: DEPLOYMENT_LAYER_ID });
  });

  test("redeclaration, unknown parents, parent cycles and conflicting positions fail", () => {
    expect(() => compileMenus([projects, { id: "projects2", menus: [{ id: "projects.tasks" }] }]))
      .toThrow(/redefines menu item id "projects.tasks"/);
    expect(() => compileMenus([{ id: "a", menus: { a: { parent: "nowhere" } } }])).toThrow(/unknown parent "nowhere"/);
    expect(() => compileMenus([{ id: "a", menus: { a: { parent: "a.b" }, "a.b": { parent: "a" } } }])).toThrow(/parent cycle/);
    expect(() => compileMenus([{ id: "a", menus: { a: {}, "a.x": { parent: "a", before: "a.y" }, "a.y": { parent: "a", before: "a.x" } } }]))
      .toThrow(/before\/after cycle/);
    expect(() => compileMenus([{ id: "a", menus: { a: {}, "a.x": { parent: "a", after: "a.nowhere" } } }]))
      .toThrow(/unknown menu item "a.nowhere"/);
  });

  test("before and after place siblings; a dangling anchor of a removed node is dropped", () => {
    const layer: MenuLayer = { id: "a", menus: {
      a: {}, "a.x": { parent: "a" }, "a.y": { parent: "a" }, "a.z": { parent: "a", before: "a.x" },
      "a.w": { parent: "a", after: "a.gone" }, "a.gone": { parent: "a", remove: true },
    } };
    expect(ids(compileMenus([layer]).logical)).toEqual([{ a: ["a.z", "a.x", "a.y", "a.w"] }]);
  });

  test("explicit sequences order unrelated layers whatever the composition order", () => {
    const one: MenuLayer = { id: "one", menus: { one: { sequence: 2 } } };
    const two: MenuLayer = { id: "two", menus: { two: { sequence: 1 } } };
    expect(ids(compileMenus([one, two]).logical)).toEqual(ids(compileMenus([two, one]).logical));
  });

  test("records who removed and hid each node, and legacy ids outside their namespace", () => {
    const product: MenuLayer = { id: "product", dependsOn: ["pm", "projects", "work"], menus: {
      work: { remove: true }, "projects.tasks": { hide: true }, pm: { only: ["projects", "pm.inbox"] },
    } };
    const stray: MenuLayer = { id: "stray", menus: [{ id: "elsewhere.page", route: "elsewhere.page" }] };
    const compiled = compileMenus([projects, work, pm, product, stray]);
    expect(compiled.removed).toContainEqual({ id: "work.cycles-hub", route: "work.cycles-hub", by: "product", parent: "pm" });
    expect(compiled.removed).toContainEqual({ id: "projects.board", route: "projects.board", by: "pm", parent: "pm" });
    expect(compiled.hidden).toEqual([{ id: "projects.tasks", by: "product", reason: "hide" }]);
    expect(compiled.provenance["projects.my-work"]).toEqual({ route: "projects", parent: "projects", sequence: "pm" });
    expect(compiled.diagnostics).toEqual(['Addon "stray" declares menu item "elsewhere.page" outside its namespace ("stray" or "stray.…").']);
  });

  test("before/after holds in the rail, accepts several nodes on one anchor and chains in any order", () => {
    const layer: MenuLayer = { id: "a", menus: {
      a: {}, "a.x": { parent: "a", sequence: 10 }, "a.y": { parent: "a", sequence: 20 },
      "a.z": { parent: "a", after: "a.x" }, "a.w": { parent: "a", after: "a.x" }, "a.v": { parent: "a", after: "a.z" },
    } };
    const compiled = compileMenus([layer]);
    expect(ids(compiled.logical)).toEqual([{ a: ["a.x", "a.z", "a.v", "a.w", "a.y"] }]);
    expect(rail(compiled.navigation)).toEqual([{ a: ["a.x", "a.z", "a.v", "a.w", "a.y"] }]);
    const chain = { a: {}, "a.x": { parent: "a", sequence: 10 }, "a.y": { parent: "a", sequence: 20 },
      "a.z": { parent: "a", after: "a.x" }, "a.v": { parent: "a", after: "a.z" } };
    for (const menus of [chain, Object.fromEntries(Object.entries(chain).reverse())]) {
      expect(ids(compileMenus([{ id: "a", menus }]).logical)).toEqual([{ a: ["a.x", "a.z", "a.v", "a.y"] }]);
    }
  });

  test("a node is positioned against a flattened app's item in the aggregator's rail", () => {
    const product: MenuLayer = { id: "product", dependsOn: ["pm"], menus: { "product.page": { parent: "pm", after: "projects.my-work" } } };
    expect(rail(compileMenus([projects, work, pm, product]).navigation)).toEqual([{ pm: [
      "pm.inbox", "projects.my-work", "product.page", "work.triage-hub", "projects.tasks", "work.cycles-hub",
    ] }]);
  });

  test("only on a flattened app narrows the items it lifts", () => {
    const narrow: MenuLayer = { id: "narrow", dependsOn: ["pm", "projects"], menus: { projects: { only: ["projects.my-work"] } } };
    const compiled = compileMenus([projects, work, pm, narrow]);
    expect(rail(compiled.navigation)).toEqual([{ pm: ["pm.inbox", "projects.my-work", "work.triage-hub", "work.cycles-hub"] }]);
    expect(compiled.hidden).toContainEqual({ id: "projects.tasks", by: "narrow", reason: "only" });
  });

  test("hidden nodes stay in the navigation tree, flagged, so the palette and admission keep them", () => {
    const product: MenuLayer = { id: "product", dependsOn: ["pm", "projects"], menus: { "projects.tasks": { hide: true } } };
    const pmNode = compileMenus([projects, work, pm, product]).navigation[0]!;
    expect(pmNode.children?.find((item) => item.id === "projects.tasks")).toMatchObject({ hidden: true });
  });

  test("the deployment places a node back at the top and refuses malformed entries", () => {
    const deployment = (menus: unknown): MenuLayer =>
      ({ id: DEPLOYMENT_LAYER_ID, dependsOn: ["projects", "work", "pm"], menus: menus as MenuLayer["menus"] });
    expect(compileMenus([projects, work, pm, deployment({ work: { parent: null } })]).logical.map((root) => root.id))
      .toEqual(["pm", "work"]);
    expect(() => compileMenus([projects, work, pm, deployment({ work: { remvoe: true } })])).toThrow(/unknown key "remvoe"/);
    expect(() => compileMenus([projects, work, pm, deployment({ pm: { only: "pm.inbox" } })])).toThrow(/only must be a list/);
    expect(() => compileMenus([projects, work, pm, deployment([{ id: "x" }])])).toThrow(/must be a mapping/);
  });

  test("requires is a declaration field: authored in both forms, set by dependents and the deployment, emitted on both trees", () => {
    const people: MenuLayer = { id: "people", menus: {
      people: { label: "People" },
      "people.manage": { parent: "people", route: "people.manage", requires: "iam.User#create" },
      "people.directory": { parent: "people", route: "people.directory" },
    } };
    const compiled = compileMenus([people]);
    for (const items of [compiled.logical, compiled.navigation]) {
      expect(items[0]?.children?.map((item) => [item.id, item.requires])).toEqual([
        ["people.manage", "iam.User#create"], ["people.directory", undefined],
      ]);
    }
    const legacy = compileMenus([{ id: "audit", menus: [{ id: "audit", requires: "iam.User#delete", children: [{ id: "audit.log", route: "audit.log" }] }] }]);
    expect(legacy.logical[0]?.requires).toBe("iam.User#delete");
    const product: MenuLayer = { id: "product", dependsOn: ["people"], menus: { "people.directory": { requires: "iam.User#read" } } };
    const deployment: MenuLayer = { id: DEPLOYMENT_LAYER_ID, dependsOn: ["people", "product"], menus: { people: { requires: "iam.Group#read" } } };
    const altered = compileMenus([people, product, deployment]);
    expect(altered.logical[0]?.requires).toBe("iam.Group#read");
    expect(altered.logical[0]?.children?.find((item) => item.id === "people.directory")?.requires).toBe("iam.User#read");
    expect(altered.provenance.people).toMatchObject({ requires: DEPLOYMENT_LAYER_ID });
    expect(() => compileMenus([{ id: "people", menus: { people: { requires: ["iam.User#create"] } } } as unknown as MenuLayer]))
      .toThrow(/requires must be a string/);
  });
});
