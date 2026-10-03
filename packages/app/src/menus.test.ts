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
    "pm.inbox": { parent: "pm", route: "messaging.inbox", sequence: 10 },
    "projects.my-work": { sequence: 20 },
    "work.triage-hub": { sequence: 30 },
    "projects.board": { remove: true },
  },
};

const ids = (items: readonly { id: string; children?: readonly { id: string }[] }[]): unknown =>
  items.map((item) => (item.children?.length ? { [item.id]: ids(item.children) } : item.id));

describe("compileMenus", () => {
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
    expect(ids(navigation)).toEqual([{ pm: [
      "pm.inbox", "projects.my-work", "work.triage-hub", "projects.tasks", "work.cycles-hub",
    ] }]);
    expect(removed).toEqual([{ id: "projects.board", route: "projects.board", by: "pm" }]);
  });

  test("removal takes the subtree; hide keeps the node in the logical tree only", () => {
    const product: MenuLayer = { id: "product", dependsOn: ["pm", "projects", "work"], menus: {
      work: { remove: true }, "projects.tasks": { hide: true },
    } };
    const { logical, navigation, removed } = compileMenus([projects, work, pm, product]);
    expect(removed.map((node) => node.id)).toEqual(["projects.board", "work", "work.triage-hub", "work.cycles-hub"]);
    expect(ids(logical)).toEqual([{ pm: ["pm.inbox", { projects: ["projects.my-work", "projects.tasks"] }] }]);
    expect(ids(navigation)).toEqual([{ pm: ["pm.inbox", "projects.my-work"] }]);
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
    expect(ids(compileMenus([projects, work, pm, narrow, top]).navigation)).toEqual([
      { pm: ["pm.inbox", "projects.my-work", "top.page"] },
    ]);
    expect(ids(compileMenus([projects, work, pm, narrow, top, unhide]).navigation)).toEqual([
      { pm: ["pm.inbox", "projects.my-work", "top.page", "projects.tasks"] },
    ]);
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
      .toThrow(/conflicting before\/after/);
  });

  test("before and after place siblings; a dangling anchor of a removed node is dropped", () => {
    const layer: MenuLayer = { id: "a", menus: {
      a: {}, "a.x": { parent: "a" }, "a.y": { parent: "a" }, "a.z": { parent: "a", before: "a.x" },
      "a.w": { parent: "a", after: "a.gone" }, "a.gone": { parent: "a", remove: true },
    } };
    expect(ids(compileMenus([layer]).logical)).toEqual([{ a: ["a.z", "a.x", "a.y", "a.w"] }]);
  });

  test("permuting unrelated layers yields the same tree", () => {
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
    expect(compiled.removed).toContainEqual({ id: "work.cycles-hub", route: "work.cycles-hub", by: "product" });
    expect(compiled.removed).toContainEqual({ id: "projects.board", route: "projects.board", by: "pm" });
    expect(compiled.hidden).toEqual([{ id: "projects.tasks", by: "product", reason: "hide" }]);
    expect(compiled.provenance["projects.my-work"]).toEqual({ route: "projects", parent: "projects", sequence: "pm" });
    expect(compiled.diagnostics).toEqual(['Addon "stray" declares menu item "elsewhere.page" outside its namespace ("stray" or "stray.…").']);
  });
});
