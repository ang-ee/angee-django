import { describe, expect, test } from "vitest";
import { chatterRouteIndex } from "./chatter-routes";
import { resourcePageRoutes } from "./define-base-addon";

describe("route chatter admission", () => {
  test("resourcePageRoutes carries admission to both collection and record routes", () => {
    const routes = chatterRouteIndex(resourcePageRoutes("example.records", "/records", () => null, undefined, {
      chatterAdmitContributions: ["comments"],
    }), []);
    expect(routes.map(({ admitContributions }) => admitContributions)).toEqual([["comments"], ["comments"]]);
  });
  test("inherits admission through route parents, allowing an explicit empty child override", () => {
    const routes = chatterRouteIndex([
      { name: "example.home", path: "/example", chatterAdmitContributions: ["comments", "activity"] },
      { name: "example.record", path: "/example/$id", parent: "example.home" },
      { name: "example.quiet", path: "/example/$id/quiet", parent: "example.record", chatterAdmitContributions: [] },
      { name: "other.home", path: "/other" },
    ], []);
    expect(routes.map(({ admitContributions }) => admitContributions)).toEqual([
      ["comments", "activity"], ["comments", "activity"], [], undefined,
    ]);
  });
});
