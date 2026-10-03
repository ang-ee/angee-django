import { describe, expect, test } from "vitest";
import { MenuTree, resolveMenuRouteTargets, type ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import { createRouteHref } from "@angee/ui/runtime";
import { resourcePageRoutes, type BaseAddonRoute } from "./define-base-addon";
import { AppRouteProjection, resourceRouteIndex, unavailableRoutes } from "./resource-projection";
import { compileMenus, type MenuLayer } from "./menus";
import { explainComposition } from "./explain";
import { resolveShell } from "./shell";
import { chatterRouteIndex } from "./chatter-routes";
import { testDataResource } from "@angee/metadata/testing";
import { resolveRoutePaths } from "./route-paths";

const Page = () => null;
const routes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes("records.all", "/records", Page, "records.Record"),
  ...resourcePageRoutes("teams.all", "/teams", Page, "teams.Team"),
  ...resourcePageRoutes("desk.incoming", "/desk/incoming", Page, "records.Record"),
  ...resourcePageRoutes("desk.review", "/desk/review", Page, undefined, { recordModel: "records.Record", param: "recordId", recordMatch: { field: "queue.id", equals: "queue-a" } }),
  { name: "desk.home", path: "/desk" },
  { name: "account", path: "/account" },
];
const menus: readonly ChromeMenuItem[] = [
  { id: "records", route: "records.all" },
  { id: "teams", route: "teams.all" },
  { id: "desk", appRoot: true, children: [
    { id: "desk.home", route: "desk.home" },
    { id: "desk.incoming", route: "desk.incoming" },
    { id: "desk.review", route: "desk.review" },
    { id: "desk.settings", group: "platform", children: [
      { id: "desk.team", route: "teams.all.record", params: { id: "team-1" } },
    ] },
  ] },
];
const menuTree = MenuTree.from(resolveMenuRouteTargets(menus, createRouteHref(routes)) as readonly ChromeMenuItem[]);

describe("app resource projection", () => {
  test("normalizes relative record paths before href and chatter projection", () => {
    const normalized = resolveRoutePaths([
      { name: "desk", path: "/desk" },
      { name: "desk.notes", path: "notes", parent: "desk", resource: "records.Record" },
      { name: "desk.note", path: "$recordId", parent: "desk.notes" },
    ]);
    const href = createRouteHref(normalized);
    expect(href("desk.note", { recordId: "r1" })).toBe("/desk/notes/r1");
    expect(chatterRouteIndex(normalized, [testDataResource("records.Record")]).at(-1)?.path).toBe("/desk/notes/$recordId");
    expect(() => resolveRoutePaths([{ name: "cycle", path: "/cycle", parent: "cycle" }])).toThrow(/cycle/);
  });
  test("retains canonical routes and selects the active collection within an app", () => {
    const projection = new AppRouteProjection(routes, menuTree);
    expect(projection.resourceRoutes()["records.Record"]?.collection).toBe("records.all");
    expect(projection.resourceRoutes("desk")["records.Record"]?.collection).toBe("desk.incoming");
    const selected = projection.resourceRoutes("desk", "desk.review.record");
    const href = createRouteHref(routes);
    expect(href(selected["records.Record"]!.record!.name, { recordId: "a/b" })).toBe("/desk/review/a%2Fb");
    expect(href(selected["teams.Team"]!.record!.name, { id: "t1" })).toBe("/teams/t1");
    expect(projection.resourceRoutes()["records.Record"]?.record?.name).toBe("records.all.record");
    expect(selected["records.Record"]?.recordDestinations).toEqual([{ record: { name: "desk.review.record", param: "recordId" }, match: { field: "queue.id", equals: "queue-a" } }]);
    expect(selected["records.Record"]?.recordFallback?.name).toBe("records.all.record");
  });

  test("confines menu and admits named owner records through their ancestor route", () => {
    const projection = new AppRouteProjection(routes, menuTree, "desk");
    expect(projection.navigationTree.railMenuItems().map((item) => item.id)).toEqual(["desk"]);
    expect(projection.navigationTree.settingsEntry()?.target).toBe("/teams/team-1");
    const teamList = routes.find((route) => route.name === "teams.all")!;
    const teamRecord = routes.find((route) => route.name === "teams.all.record")!;
    expect(projection.allows(teamList, "/teams/team-1")).toBe(true);
    expect(projection.allows(teamRecord, "/teams/team-1")).toBe(true);
    expect(projection.allows(teamRecord, "/teams/team-2")).toBe(false);
    expect(projection.allows(teamList, "/teams")).toBe(false);
    expect(projection.allows(routes[0]!, "/records/r1")).toBe(false);
    expect(projection.allows(routes.at(-1)!, "/account")).toBe(true);
    expect(projection.navigationTree.activeItem("/desk/review/r1")?.id).toBe("desk.review");
  });

  test("confineTo supplies the app scope even without an explicit appRoot marker", () => {
    const tree = MenuTree.from(resolveMenuRouteTargets(menus.map((item) => ({ ...item, appRoot: undefined })), createRouteHref(routes)) as readonly ChromeMenuItem[]);
    const projection = new AppRouteProjection(routes, tree, "desk");
    expect(projection.resourceRoutes("desk")["records.Record"]?.collection).toBe("desk.incoming");
  });

  test("a collection-only app projection retains canonical record fallback", () => {
    const collectionRoutes: BaseAddonRoute[] = [
      ...resourcePageRoutes("records.all", "/records", Page, "records.Record"),
      { name: "desk.all", path: "/desk", recordModel: "records.Record" },
    ];
    const tree = MenuTree.from([
      { id: "records", route: "records.all", to: "/records" },
      { id: "desk", route: "desk.all", to: "/desk", appRoot: true },
    ]);
    const selected = new AppRouteProjection(collectionRoutes, tree).resourceRoutes("desk");
    expect(selected["records.Record"]?.collection).toBe("desk.all");
    expect(selected["records.Record"]?.record?.name).toBe("records.all.record");
  });

  test("rejects duplicate canonical claims and ambiguous record children", () => {
    expect(() => resourceRouteIndex([
      ...resourcePageRoutes("one", "/one", Page, "records.Record"),
      ...resourcePageRoutes("two", "/two", Page, "records.Record"),
    ])).toThrow(/already claimed/);
    expect(() => resourceRouteIndex([
      ...resourcePageRoutes("one", "/one", Page, "records.Record"),
      { name: "extra", path: "/one/$other", parent: "one" },
    ])).toThrow(/multiple record routes/);
  });

  test("record projections retain model-aware aside metadata", () => {
    const index = chatterRouteIndex(routes, [testDataResource("records.Record"), testDataResource("teams.Team")]);
    expect(index.find((route) => route.name === "desk.review.record")).toMatchObject({
      modelLabel: "records.Record", recordParam: "recordId", viewType: "records/record",
    });
  });
});

describe("menu alterations in the route projection", () => {
  const deskRoutes: readonly BaseAddonRoute[] = [
    ...routes,
    { name: "desk.cycles-hub", path: "/desk/cycles" },
    { name: "desk.cycles", path: "/desk/queues/$queueId/cycles", menu: "desk.cycles-hub" },
    { name: "desk.cycle", path: "$cycleId", parent: "desk.cycles" },
  ];
  const layers: MenuLayer[] = [
    { id: "records", menus: [{ id: "records", route: "records.all" }] },
    { id: "teams", menus: [{ id: "teams", route: "teams.all" }] },
    { id: "desk", dependsOn: ["records", "teams"], menus: [{ id: "desk", children: [
      { id: "desk.home", route: "desk.home" },
      { id: "desk.incoming", route: "desk.incoming" },
      { id: "desk.review", route: "desk.review" },
      { id: "desk.cycles-hub", route: "desk.cycles-hub" },
      { id: "desk.settings", group: "platform", children: [{ id: "desk.team", route: "teams.all.record", params: { id: "team-1" } }] },
    ] }] },
    { id: "suite", dependsOn: ["desk", "records", "teams"], menus: {
      suite: { label: "Suite", appRoot: true, include: [{ id: "desk", flatten: true }, "records"] },
      "desk.review": { remove: true },
      "desk.cycles-hub": { remove: true },
      "desk.incoming": { hide: true },
    } },
  ];
  const compiled = compileMenus(layers);
  const href = createRouteHref(deskRoutes);
  const logical = MenuTree.from(resolveMenuRouteTargets(compiled.logical, href) as readonly ChromeMenuItem[]);
  const navigation = MenuTree.from(resolveMenuRouteTargets(compiled.navigation, href) as readonly ChromeMenuItem[]);
  const unavailable = unavailableRoutes(deskRoutes, logical, compiled.removed);
  const route = (name: string) => deskRoutes.find((candidate) => candidate.name === name)!;

  test("removal disables targeted routes, hub-anchored pages and their descendants; hide disables nothing", () => {
    expect([...unavailable.keys()].sort()).toEqual(["desk.cycle", "desk.cycles", "desk.cycles-hub", "desk.review", "desk.review.record"]);
    expect(unavailable.get("desk.cycles")).toBe('its menu anchor "desk.cycles-hub" was removed');
    expect(unavailable.get("desk.cycle")).toBe('its parent route "desk.cycles" is unavailable');
    expect(unavailable.get("desk.review")).toBe('menu item "desk.review" was removed');
  });

  test("the guard refuses unavailable routes, also without a perspective", () => {
    const projection = new AppRouteProjection(deskRoutes, logical, undefined, { navigation, removed: compiled.removed });
    expect(projection.allows(route("desk.cycles"), "/desk/queues/q1/cycles")).toBe(false);
    expect(projection.allows(route("desk.cycle"), "/desk/queues/q1/cycles/c1")).toBe(false);
    expect(projection.allows(route("desk.incoming"), "/desk/incoming")).toBe(true);
  });

  test("claims and record destinations skip removed pages; hidden pages keep their claims", () => {
    const projection = new AppRouteProjection(deskRoutes, logical, "suite", { navigation, removed: compiled.removed });
    expect(projection.rootFor(route("desk.incoming"))).toBe("suite");
    const selected = projection.resourceRoutes("suite");
    expect(selected["records.Record"]?.collection).toBe("desk.incoming");
    expect(selected["records.Record"]?.recordDestinations).toBeUndefined();
    expect(selected["records.Record"]?.record?.name).toBe("desk.incoming.record");
  });

  test("navigation flattens the included app and drops hidden items; the logical tree keeps them", () => {
    const projection = new AppRouteProjection(deskRoutes, logical, "suite", { navigation, removed: compiled.removed });
    expect(projection.navigationTree.railMenuItems().map((item) => item.id)).toEqual(["suite"]);
    expect(projection.navigationTree.byId.get("suite")?.targetedChildren.map((item) => item.id)).toEqual(["desk.home", "records"]);
    expect(projection.navigationTree.settingsEntry()?.target).toBe("/teams/team-1");
    expect(logical.trailFor("desk.incoming").map((item) => item.id)).toEqual(["suite", "desk", "desk.incoming"]);
    // Hidden: out of the rail, still in the navigation tree for the palette and admission.
    expect(projection.navigationTree.byId.get("desk.incoming")?.hidden).toBe(true);
  });
});

describe("composition explanation", () => {
  test("collects shell provenance, menu removals, hidden nodes and unavailable routes with reasons", () => {
    const layers: MenuLayer[] = [
      { id: "desk", menus: [{ id: "desk", children: [{ id: "desk.home", route: "desk.home" }, { id: "desk.review", route: "desk.review" }] }] },
      { id: "suite", dependsOn: ["desk"], menus: { "desk.review": { remove: true }, "desk.home": { hide: true } } },
    ];
    const compiled = compileMenus(layers);
    const href = createRouteHref(routes);
    const logical = MenuTree.from(resolveMenuRouteTargets(compiled.logical, href) as readonly ChromeMenuItem[]);
    const explanation = explainComposition(resolveShell(layers), compiled, unavailableRoutes(routes, logical, compiled.removed), { home: "/desk", confineTo: null });
    expect(explanation.menus.removed).toEqual([{ id: "desk.review", route: "desk.review", by: "suite", parent: "desk" }]);
    expect(explanation.menus.hidden).toEqual([{ id: "desk.home", by: "suite", reason: "hide" }]);
    expect(explanation.menus.unavailable).toEqual({
      "desk.review": 'menu item "desk.review" was removed',
      "desk.review.record": 'its parent route "desk.review" is unavailable',
    });
    expect(explanation.shell).toEqual({ brand: null, perspective: null, provenance: {}, diagnostics: [] });
    expect(explanation.effective).toEqual({ home: "/desk", confineTo: null });
  });
});

describe("availability edge cases", () => {
  const anchored: readonly BaseAddonRoute[] = [
    { name: "inbox", path: "/inbox", menu: "decisions" },
    { name: "pageant", path: "/pageant", layout: "fullscreen" },
  ];
  test("a surviving reference keeps a route whose anchor was removed available", () => {
    const tree = MenuTree.from(resolveMenuRouteTargets([{ id: "pm.inbox", route: "inbox" }], createRouteHref(anchored)) as readonly ChromeMenuItem[]);
    const projection = new AppRouteProjection(anchored, tree, undefined, { removed: [{ id: "decisions", route: "inbox" }] });
    expect(projection.unavailable.size).toBe(0);
    expect(projection.allows(anchored[0]!, "/inbox")).toBe(true);
  });
  test("only console routes become unavailable; an anchor naming nothing is a wiring error", () => {
    const tree = MenuTree.from([]);
    expect(unavailableRoutes(anchored.slice(1), tree, [{ id: "x", route: "pageant" }]).size).toBe(0);
    expect(() => unavailableRoutes([{ name: "lost", path: "/lost", menu: "typo" }], tree, []))
      .toThrow(/references unknown menu item "typo"/);
  });
});
