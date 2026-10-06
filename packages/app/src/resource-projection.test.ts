import { describe, expect, test } from "vitest";
import { MenuTree, resolveMenuRouteTargets, type ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import { createRouteHref } from "@angee/ui/runtime";
import { resourcePageRoutes, type BaseAddonRoute } from "./define-base-addon";
import { AppRouteProjection, menuNodeForRoute, refineRouteResourceProjection, resourceRouteIndex, unavailableRoutes } from "./resource-projection";
import { chromeMenuItemsFromRefine } from "@angee/ui/chrome/refine-menu";
import type { TreeMenuItem } from "@refinedev/core";
import { compileMenus, type MenuLayer } from "./menus";
import { explainComposition } from "./explain";
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
  { name: "appearance", path: "/settings/appearance" },
  { name: "platform", path: "/settings/platform" },
  { name: "account", path: "/account" },
];
const menus: readonly ChromeMenuItem[] = [
  { id: "records", route: "records.all" },
  { id: "teams", route: "teams.all" },
  { id: "desk", children: [
    { id: "desk.home", route: "desk.home" },
    { id: "desk.incoming", route: "desk.incoming" },
    { id: "desk.review", route: "desk.review" },
    { id: "desk.settings", group: "platform", children: [
      { id: "desk.team", route: "teams.all.record", params: { id: "team-1" } },
    ] },
  ] },
  { id: "appearance", group: "platform", personal: true, route: "appearance" },
  { id: "platform", group: "platform", route: "platform" },
];
const menuTree = MenuTree.from(resolveMenuRouteTargets(menus, createRouteHref(routes)) as readonly ChromeMenuItem[]);

describe("app resource projection", () => {
  test("the refine bridge preserves a route-less Settings group and its single child's own target", () => {
    const route = { name: "tags.all", path: "/tags" };
    const menus: readonly ChromeMenuItem[] = [{ id: "tags", label: "Tags", group: "platform", children: [
      { id: "tags.all", label: "All tags", route: "tags.all" },
    ] }];
    const tree = MenuTree.from(resolveMenuRouteTargets(menus, createRouteHref([route])));
    const resources = refineRouteResourceProjection([route], tree, tree).resources;
    const group = resources.find((item) => item.meta?.menuId === "tags")!;
    const page = resources.find((item) => item.meta?.menuId === "tags.all")!;
    expect(group.list).toBe("/tags");
    expect(group.meta?.menuTarget).toBeNull();
    expect(page.meta?.menuTarget).toBe("/tags");
    const chrome = MenuTree.from(chromeMenuItemsFromRefine([{
      key: "menu:tags", name: "menu:tags", route: group.list as string, meta: group.meta,
      children: [{ key: "menu:tags.all", name: "menu:tags.all", route: page.list as string, meta: page.meta, children: [] }],
    }]));
    expect(chrome.roots[0]?.to).toBeUndefined();
    expect(chrome.roots[0]?.target).toBe("/tags");
    expect(chrome.match("/tags")?.item.id).toBe("tags.all");
  });

  test("the full console lifts platform nodes, retaining logical ownership and included app identity through the refine bridge", () => {
    const compiled = compileMenus([
      { id: "mail", menus: {
        mail: {},
        "mail.messages": { parent: "mail", route: "mail.messages" },
        "mail.channels": { parent: "mail", route: "mail.channels", group: "platform" },
      } },
      { id: "suite", dependsOn: ["mail"], menus: { suite: { include: ["mail"] } } },
    ]);
    const routes = [
      { name: "mail.messages", path: "/mail/messages", layout: "console" },
      { name: "mail.channels", path: "/mail/channels", layout: "console", resource: "mail.Channel" },
      { name: "mail.channels.record", path: "/mail/channels/$id", layout: "console", parent: "mail.channels" },
    ];
    const href = createRouteHref(routes);
    const logical = MenuTree.from(resolveMenuRouteTargets(compiled.logical, href));
    const projection = new AppRouteProjection(routes, logical, undefined, {
      navigation: MenuTree.from(resolveMenuRouteTargets(compiled.navigation, href)),
    });
    expect(projection.homeApp).toBeUndefined();
    expect(projection.navigationTree.settingsMenuItems().map(({ id }) => id)).toEqual(["mail.channels"]);
    expect(projection.navigationTree.byId.get("mail")?.menuItems().map(({ id }) => id)).toEqual(["mail.messages"]);
    expect(projection.navigationTree.railPlace("/mail/channels/one")).toMatchObject({ scope: "settings", activeRootId: "mail.channels" });
    expect(logical.trailFor("mail.channels").map(({ id }) => id)).toEqual(["suite", "mail", "mail.channels"]);
    expect(projection.rootFor(routes[1]!)).toBe("suite");
    expect(projection.resourceRoutes()["mail.Channel"]).toEqual({ collection: "mail.channels", record: { name: "mail.channels.record", param: "id" } });
    expect(projection.unavailable.size).toBe(0);
    expect(compiled.provenance["mail.channels"]?.group).toBe("mail");

    const projected = refineRouteResourceProjection(routes, logical, projection.navigationTree).resources;
    const mail = projected.find((item) => item.meta?.menuId === "mail")!;
    const messages = projected.find((item) => item.meta?.menuId === "mail.messages")!;
    expect(mail.meta).toMatchObject({ app: true, parent: "menu:suite" });
    expect(messages.meta?.app).toBeUndefined();
    const refineNode = (resource: typeof mail, children: TreeMenuItem[] = []): TreeMenuItem => ({
      name: resource.name, key: resource.identifier ?? resource.name,
      label: resource.meta?.label, route: resource.list as string, meta: resource.meta, children,
    });
    const suite = projected.find((item) => item.meta?.menuId === "suite")!;
    const channels = projected.find((item) => item.meta?.menuId === "mail.channels")!;
    const chrome = MenuTree.from(chromeMenuItemsFromRefine([
      refineNode(suite, [refineNode(mail, [refineNode(messages)])]), refineNode(channels),
    ]));
    expect(chrome.byId.get("mail")?.isApp).toBe(true);
    expect(chrome.match("/mail/messages")?.app?.id).toBe("mail");
    expect(chrome.trailFor("mail.channels").map(({ id }) => id)).toEqual(["mail.channels"]);
  });
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
    const projection = new AppRouteProjection(routes, menuTree, { rail: ["desk"] });
    expect(projection.resourceRoutes()["records.Record"]?.collection).toBe("records.all");
    expect(projection.resourceRoutes("desk")["records.Record"]?.collection).toBe("desk.incoming");
    const selected = projection.resourceRoutes("desk", "desk.review.record");
    const href = createRouteHref(routes);
    expect(href(selected["records.Record"]!.record!.name, { recordId: "a/b" })).toBe("/desk/review/a%2Fb");
    expect(href(selected["teams.Team"]!.record!.name, { id: "t1" })).toBe("/teams/t1");
    expect(projection.resourceRoutes()["records.Record"]?.record?.name).toBe("records.all.record");
    expect(selected["records.Record"]?.recordDestinations).toEqual([{ record: { name: "desk.review.record", param: "recordId" }, match: { field: "queue.id", equals: "queue-a" } }]);
    // A row no match claims opens at the app's own claim, not at the matching route the page sits under.
    expect(selected["records.Record"]?.recordFallback?.name).toBe("desk.incoming.record");
  });

  test("a record match claims its rows from every app; the app's own claim, then the canonical route, takes the rest", () => {
    const projection = new AppRouteProjection(routes, menuTree, { rail: ["desk"] });
    const destinations = [{ record: { name: "desk.review.record", param: "recordId" }, match: { field: "queue.id", equals: "queue-a" } }];
    expect(projection.resourceRoutes()["records.Record"]).toEqual({
      collection: "records.all", record: { name: "records.all.record", param: "id" },
      recordDestinations: destinations, recordFallback: { name: "records.all.record", param: "id" },
    });
    expect(projection.resourceRoutes("teams")["records.Record"]).toMatchObject({ recordDestinations: destinations, recordFallback: { name: "records.all.record" } });
    expect(projection.resourceRoutes("desk")["records.Record"]).toMatchObject({ recordDestinations: destinations, recordFallback: { name: "desk.incoming.record" } });
    // Resources no match claims carry no destinations.
    expect(projection.resourceRoutes()["teams.Team"]?.recordDestinations).toBeUndefined();
  });

  test("two record matches on one resource and condition fail at boot, whichever apps declare them", () => {
    const claimed: readonly BaseAddonRoute[] = [
      ...routes,
      ...resourcePageRoutes("mine.records", "/mine", Page, undefined, { recordModel: "records.Record", recordMatch: { field: "queue.id", equals: "queue-a" } }),
    ];
    const tree = (extra: readonly ChromeMenuItem[]) =>
      MenuTree.from(resolveMenuRouteTargets([...menus, ...extra], createRouteHref(claimed)) as readonly ChromeMenuItem[]);
    // "mine" is off the rail and "desk" on it: a match claims globally either way.
    expect(() => new AppRouteProjection(claimed, tree([{ id: "mine", route: "mine.records" }]), { rail: ["desk"] }))
      .toThrow(/Resource "records.Record" has duplicate record match "queue.id=queue-a"/);
    const other = claimed.map((route) => route.name === "mine.records" ? { ...route, recordMatch: { field: "queue.id", equals: "queue-b" } } : route);
    expect(() => new AppRouteProjection(other, tree([{ id: "mine", route: "mine.records" }]), { rail: ["desk"] })).not.toThrow();
    expect(() => new AppRouteProjection([
      { name: "mine.records", path: "/mine", recordModel: "records.Record", recordMatch: { field: "queue.id", equals: "queue-a" } },
    ], MenuTree.from([]))).toThrow(/declares recordMatch without a record child/);
  });

  test("confines the menu to the rail; a page outside it sits in the home app, a page inside in its own root", () => {
    const projection = new AppRouteProjection(routes, menuTree, { rail: ["desk"], home: "desk.incoming" });
    expect(projection.homeApp).toBe("desk");
    expect(projection.navigationTree.railMenuItems().map((item) => item.id)).toEqual(["desk"]);
    expect(projection.navigationTree.settingsEntry()?.target).toBe("/teams/team-1");
    expect(projection.navigationTree.activeItem("/desk/review/r1")?.id).toBe("desk.review");
    // The rail keeps the personal Settings roots; other roots leave the navigation, not the router.
    expect(projection.navigationTree.settingsMenuItems().map((item) => item.id)).toEqual(["desk.settings", "appearance"]);
    expect(projection.navigationTree.byId.has("platform")).toBe(false);
    expect(projection.activeApp("/desk/review/r1")).toBe("desk");
    expect(projection.activeApp("/records/r1")).toBe("desk");
    expect(projection.activeApp("/settings/platform")).toBe("desk");
    expect(projection.appTrail("/records/r1")).toEqual(["desk"]);
    // A rail of several roots: each keeps its own pages; the home app is its first root without a home.
    const both = new AppRouteProjection(routes, menuTree, { rail: ["records", "desk"] });
    expect(both.homeApp).toBe("records");
    expect(both.navigationTree.railMenuItems().map((item) => item.id)).toEqual(["records", "desk"]);
    expect(both.activeApp("/records/r1")).toBe("records");
    expect(both.activeApp("/desk/incoming")).toBe("desk");
    expect(both.activeApp("/account")).toBe("records");
    expect(both.resourceRoutes("records")["records.Record"]?.collection).toBe("records.all");
    expect(both.resourceRoutes("desk")["records.Record"]?.collection).toBe("desk.incoming");
  });

  test("the app trail lists every app a page sits in, flattened ones too (G-8)", () => {
    const compiled = compileMenus([
      { id: "projects", menus: [{ id: "projects", children: [{ id: "projects.tasks", route: "projects.tasks" }] }] },
      { id: "messaging", menus: { messaging: { route: "messaging.messages" } } },
      { id: "pm", dependsOn: ["projects", "messaging"], menus: { pm: { include: [{ id: "projects", flatten: true }, "messaging"] } } },
      { id: "look", menus: { look: { route: "look.page", group: "platform" } } },
    ]);
    const appRoutes: readonly BaseAddonRoute[] = [
      { name: "projects.tasks", path: "/projects/tasks" },
      { name: "messaging.messages", path: "/messaging" },
      { name: "look.page", path: "/settings/look" },
    ];
    const tree = MenuTree.from(resolveMenuRouteTargets(compiled.logical, createRouteHref(appRoutes)) as readonly ChromeMenuItem[]);
    const projection = new AppRouteProjection(appRoutes, tree);
    expect(projection.appTrail("/projects/tasks/t1")).toEqual(["pm", "projects"]);
    expect(projection.appTrail("/messaging")).toEqual(["pm", "messaging"]);
    expect(projection.appTrail("/elsewhere")).toEqual([]);
    // A Settings root is no app.
    expect(projection.appTrail("/settings/look")).toEqual([]);
    // Under a selected app a rail root's own apps count; a page outside the rail sits in the home root alone.
    const selected = new AppRouteProjection(appRoutes, tree, { rail: ["pm"] });
    expect(selected.appTrail("/projects/tasks")).toEqual(["pm", "projects"]);
    expect(selected.appTrail("/settings/look")).toEqual(["pm"]);
    expect(tree.appIds()).toEqual(new Set(["projects", "messaging", "pm"]));
  });

  test("only rail roots claim their own resource routes; elsewhere two routes of one resource collide", () => {
    expect(new AppRouteProjection(routes, menuTree, { rail: ["desk"] }).resourceRoutes("desk")["records.Record"]?.collection).toBe("desk.incoming");
    expect(() => new AppRouteProjection(routes, menuTree, { rail: ["teams"] })).toThrow(/claims resource "records.Record" already claimed/);
    expect(() => new AppRouteProjection(routes, menuTree)).toThrow(/claims resource "records.Record" already claimed/);
  });

  test("a collection-only app projection retains canonical record fallback", () => {
    const collectionRoutes: BaseAddonRoute[] = [
      ...resourcePageRoutes("records.all", "/records", Page, "records.Record"),
      { name: "desk.all", path: "/desk", recordModel: "records.Record" },
    ];
    const tree = MenuTree.from([
      { id: "records", route: "records.all", to: "/records" },
      { id: "desk", route: "desk.all", to: "/desk" },
    ]);
    const selected = new AppRouteProjection(collectionRoutes, tree, { rail: ["desk"] }).resourceRoutes("desk");
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
      suite: { label: "Suite", include: [{ id: "desk", flatten: true }, "records"] },
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

  test("claims and record destinations skip removed pages; hidden pages keep their claims", () => {
    const projection = new AppRouteProjection(deskRoutes, logical, { rail: ["suite"] }, { navigation, removed: compiled.removed });
    expect(projection.rootFor(route("desk.incoming"))).toBe("suite");
    const selected = projection.resourceRoutes("suite");
    expect(selected["records.Record"]?.collection).toBe("desk.incoming");
    expect(selected["records.Record"]?.recordDestinations).toBeUndefined();
    expect(selected["records.Record"]?.record?.name).toBe("desk.incoming.record");
  });

  test("navigation flattens the included app and drops hidden items; the logical tree keeps them", () => {
    const projection = new AppRouteProjection(deskRoutes, logical, { rail: ["suite"] }, { navigation, removed: compiled.removed });
    expect(projection.navigationTree.railMenuItems().map((item) => item.id)).toEqual(["suite"]);
    expect(projection.navigationTree.byId.get("suite")?.targetedChildren.map((item) => item.id)).toEqual(["desk.home", "records"]);
    expect(projection.navigationTree.settingsEntry()?.target).toBe("/teams/team-1");
    expect(logical.trailFor("desk.incoming").map((item) => item.id)).toEqual(["suite", "desk", "desk.incoming"]);
    // Hidden: out of the rail, still in the navigation tree for the palette and admission.
    expect(projection.navigationTree.byId.get("desk.incoming")?.hidden).toBe(true);
  });
});

describe("composition explanation", () => {
  test("collects the selection, menu removals, hidden nodes and unavailable routes with reasons", () => {
    const layers: MenuLayer[] = [
      { id: "desk", menus: [{ id: "desk", children: [{ id: "desk.home", route: "desk.home" }, { id: "desk.review", route: "desk.review" }] }] },
      { id: "suite", dependsOn: ["desk"], menus: { "desk.review": { remove: true }, "desk.home": { hide: true } } },
    ];
    const compiled = compileMenus(layers);
    const href = createRouteHref(routes);
    const logical = MenuTree.from(resolveMenuRouteTargets(compiled.logical, href) as readonly ChromeMenuItem[]);
    const selection = { app: null, rail: null, brand: null, sources: {}, diagnostics: [] };
    const explanation = explainComposition(selection, compiled, unavailableRoutes(routes, logical, compiled.removed), "/desk");
    expect(explanation.menus.removed).toEqual([{ id: "desk.review", route: "desk.review", by: "suite", parent: "desk" }]);
    expect(explanation.menus.hidden).toEqual([{ id: "desk.home", by: "suite", reason: "hide" }]);
    expect(explanation.menus.unavailable).toEqual({
      "desk.review": 'menu item "desk.review" was removed',
      "desk.review.record": 'its parent route "desk.review" is unavailable',
    });
    expect(explanation.selection).toBe(selection);
    expect(explanation.home).toBe("/desk");
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
  });
  test("a removed anchor does not hand a parameterized route to one of its destinations", () => {
    const boards: readonly BaseAddonRoute[] = [{ name: "boards.board", path: "/boards/$key", menu: "boards" }];
    const tree = MenuTree.from(resolveMenuRouteTargets(
      [{ id: "boards.main", route: "boards.board", params: { key: "main" } }], createRouteHref(boards),
    ) as readonly ChromeMenuItem[]);
    expect(menuNodeForRoute(boards[0]!, tree)).toBeUndefined();
  });
  test("only console routes become unavailable; an anchor naming nothing is a wiring error", () => {
    const tree = MenuTree.from([]);
    expect(unavailableRoutes(anchored.slice(1), tree, [{ id: "x", route: "pageant" }]).size).toBe(0);
    expect(() => unavailableRoutes([{ name: "lost", path: "/lost", menu: "typo" }], tree, []))
      .toThrow(/references unknown menu item "typo"/);
  });
});

describe("presence in the navigation", () => {
  const presenceRoutes: readonly BaseAddonRoute[] = [
    { name: "people.directory", path: "/people" },
    { name: "people.manage", path: "/people/manage" },
    { name: "people.manage.record", path: "$id", parent: "people.manage" },
    { name: "people.review", path: "/people/review" },
    { name: "audit.log", path: "/audit/log" },
    { name: "ledger.entries", path: "/ledger/entries" },
    { name: "suite.home", path: "/suite" },
  ];
  const compiled = compileMenus([
    { id: "people", menus: {
      people: { label: "People" },
      "people.directory": { parent: "people", route: "people.directory" },
      "people.manage": { parent: "people", route: "people.manage", requires: "iam.User#create" },
      "people.reviews": { parent: "people", requires: "iam.User#read__last_login" },
      "people.review": { parent: "people.reviews", route: "people.review" },
    } },
    { id: "audit", menus: {
      audit: { label: "Audit", requires: "iam.User#delete" },
      "audit.log": { parent: "audit", route: "audit.log" },
    } },
    { id: "ledger", menus: {
      ledger: { label: "Ledger", requires: "ledger.Entry#read" },
      "ledger.entries": { parent: "ledger", route: "ledger.entries" },
    } },
    { id: "suite", dependsOn: ["ledger"], menus: {
      suite: { label: "Suite", include: [{ id: "ledger", flatten: true }] },
      "suite.home": { parent: "suite", route: "suite.home" },
    } },
  ]);
  const href = createRouteHref(presenceRoutes);
  const logical = MenuTree.from(resolveMenuRouteTargets(compiled.logical, href) as readonly ChromeMenuItem[]);
  const navigation = MenuTree.from(resolveMenuRouteTargets(compiled.navigation, href) as readonly ChromeMenuItem[]);
  const projection = new AppRouteProjection(presenceRoutes, logical, undefined, { navigation, removed: compiled.removed });
  const route = (name: string) => presenceRoutes.find((candidate) => candidate.name === name)!;
  const palette = (tree: MenuTree) => tree.navigableItems().map(({ item }) => item.id).sort();
  const menuResources = (tree: MenuTree) =>
    refineRouteResourceProjection(presenceRoutes, logical, tree).resources.map((resource) => resource.meta?.menuId).sort();
  const every = ["iam.User#create", "iam.User#delete", "iam.User#read__last_login", "ledger.Entry#read"];

  test("without the session's refs, and holding every one, the navigation stands as declared", () => {
    expect(projection.navigationFor(undefined)).toBe(projection.navigationTree);
    expect(projection.navigationFor(every)).toBe(projection.navigationTree);
    expect(palette(projection.navigationTree)).toEqual(["audit.log", "ledger.entries", "people.directory", "people.manage", "people.review", "suite.home"]);
  });

  test("a node the session lacks a ref for leaves the rail, menus and palette with its subtree; its routes stay", () => {
    const none = projection.navigationFor([]);
    expect(none.railMenuItems().map((item) => item.id)).toEqual(["people", "suite"]);
    expect(palette(none)).toEqual(["people.directory", "suite.home"]);
    // A flattened app's items leave with it, though the navigation lifts them under the suite.
    for (const id of ["people.manage", "people.reviews", "people.review", "audit", "audit.log", "ledger.entries"]) {
      expect(none.byId.has(id)).toBe(false);
    }
    expect(menuResources(none)).toEqual(["people", "people.directory", "suite", "suite.home"]);
    // Presence only: availability, route ownership and app scope are the declared composition's.
    expect(projection.unavailable.size).toBe(0);
    expect(projection.rootFor(route("people.manage"))).toBe("people");
    expect(projection.rootFor(route("people.manage.record"))).toBe("people");
    expect(projection.activeApp("/audit/log", "audit.log")).toBe("audit");
    // The active entry is one the session sees.
    expect(projection.activeMenu("/people/manage/p1", "people.manage.record")?.item.id).toBe("people.manage");
    expect(projection.activeMenu("/people/manage/p1", "people.manage.record", undefined, none)?.item.id).toBe("people.directory");
  });

  test("each ref held brings back exactly what requires it, one tree per set of refs", () => {
    expect(palette(projection.navigationFor(["iam.User#create"]))).toEqual(["people.directory", "people.manage", "suite.home"]);
    expect(palette(projection.navigationFor(["ledger.Entry#read"]))).toEqual(["ledger.entries", "people.directory", "suite.home"]);
    expect(projection.navigationFor(["iam.User#delete", "iam.User#create"]))
      .toBe(projection.navigationFor(["iam.User#create", "iam.User#delete"]));
  });
});
