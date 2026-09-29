import { describe, expect, test } from "vitest";
import { chatterRouteIndex } from "./chatter-routes";
import { resourcePageRoutes } from "./define-base-addon";

describe("route chatter policy", () => {
  test("resourcePageRoutes carries tab selection to its record route", () => {
    const routes = chatterRouteIndex(resourcePageRoutes("example.records", "/records", () => null, undefined, {
      chatter: { tabs: ["comments"] },
    }), []);
    expect(routes.map(({ chatter }) => chatter)).toEqual([{ tabs: ["comments"] }, { tabs: ["comments"] }]);
  });
  test("inherits policy through parents, allowing an explicit empty child override", () => {
    const routes = chatterRouteIndex([
      { name: "example.home", path: "/example", chatter: { tabs: ["comments", "activity"] } },
      { name: "example.record", path: "/example/$id", parent: "example.home" },
      { name: "example.quiet", path: "/example/$id/quiet", parent: "example.record", chatter: { tabs: [] } },
      { name: "other.home", path: "/other", chatter: "hidden" },
    ], []);
    expect(routes.map(({ chatter }) => chatter)).toEqual([
      { tabs: ["comments", "activity"] }, { tabs: ["comments", "activity"] }, { tabs: [] }, "hidden",
    ]);
  });
});
