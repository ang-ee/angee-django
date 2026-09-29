import { describe, expect, test } from "vitest";
import { MenuTree, resolveMenuRouteTargets, type ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import { createRouteHref } from "@angee/ui/runtime";
import { resourcePageRoutes, type BaseAddonRoute } from "./define-base-addon";
import { AppRouteProjection, resourceRouteIndex } from "./resource-projection";
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

  test("record projections inherit model-aware chatter and per-route policy", () => {
    const index = chatterRouteIndex(routes, [testDataResource("records.Record"), testDataResource("teams.Team")]);
    expect(index.find((route) => route.name === "desk.review.record")).toMatchObject({
      modelLabel: "records.Record", recordParam: "recordId", viewType: "records/record", chatter: undefined,
    });
    const hidden = chatterRouteIndex(resourcePageRoutes("quiet", "/quiet", Page, "records.Record", { chatter: "hidden" }), [testDataResource("records.Record")]);
    expect(hidden.map((route) => route.chatter)).toEqual(["hidden", "hidden"]);
  });
});
