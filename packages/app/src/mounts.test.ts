import { describe, expect, test } from "vitest";

import { resourcePageRoutes, type BaseAddonRoute } from "./define-base-addon";
import { mountRoutes } from "./mounts";
import { resolveRoutePaths } from "./route-paths";

const List = () => null;
const Detail = () => null;
const routes = resolveRoutePaths([
  ...resourcePageRoutes("iam.users", "/iam/users", List, "iam.User", {
    menu: "iam.users", detailComponent: Detail, defaultResourceView: "iam.active",
  }),
  { name: "iam.home", path: "/iam", component: List },
  { name: "iam.settings", path: "/iam/settings/$tab", component: List },
]);

describe("mountRoutes", () => {
  test("an alias and its record child reuse the mounted pages at the mount's path, anchored to its node", () => {
    const mounted = mountRoutes(routes, [
      { id: "desk.people", route: "iam.users", path: "/desk/people", by: "desk", recordMatch: { field: "kind", equals: "staff" } },
    ]);
    expect(mounted.routes.slice(0, routes.length)).toEqual(routes);
    // The model becomes a projection (`recordModel`): the canonical claim stays with the owner.
    expect(mounted.routes.slice(routes.length)).toEqual([
      { name: "desk.people", path: "/desk/people", menu: "desk.people", layout: "console", indexComponent: List,
        recordModel: "iam.User", defaultResourceView: "iam.active", recordMatch: { field: "kind", equals: "staff" } },
      { name: "desk.people.record", path: "/desk/people/$id", parent: "desk.people", component: Detail },
    ]);
    expect(mounted.families).toEqual([new Map([["iam.users", "desk.people"], ["iam.users.record", "desk.people.record"]])]);
    // The mount's own default view wins over the mounted route's.
    const staff = mountRoutes(routes, [{ id: "desk.staff", route: "iam.users", path: "/desk/staff", by: "desk", defaultResourceView: "desk.staff" }]);
    expect(staff.routes.find((route) => route.name === "desk.staff")?.defaultResourceView).toBe("desk.staff");
  });

  test("a page without a record child mounts alone", () => {
    const mounted = mountRoutes(routes, [{ id: "desk.home", route: "iam.home", path: "/desk/home", by: "desk" }]);
    expect(mounted.routes.slice(routes.length)).toEqual([
      { name: "desk.home", path: "/desk/home", menu: "desk.home", component: List },
    ] satisfies BaseAddonRoute[]);
    expect(mounted.families).toEqual([new Map([["iam.home", "desk.home"]])]);
  });

  test("parameterized, unknown and already named routes fail", () => {
    expect(() => mountRoutes(routes, [{ id: "desk.tab", route: "iam.settings", path: "/desk/tab", by: "desk" }]))
      .toThrow(/mounts parameterized route "iam.settings"/);
    expect(() => mountRoutes(routes, [{ id: "desk.ghost", route: "iam.ghost", path: "/desk/ghost", by: "desk" }]))
      .toThrow(/mounts unknown route "iam.ghost"/);
    expect(() => mountRoutes(routes, [{ id: "iam.home", route: "iam.users", path: "/desk/people", by: "desk" }]))
      .toThrow(/as route "iam.home", which another route already names/);
  });
});
