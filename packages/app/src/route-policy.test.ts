import { describe, expect, test } from "vitest";
import { MenuTree } from "@angee/ui/chrome/menu-tree";

import { admittedContributions, routePolicyIndex } from "./route-policy";

const menus = MenuTree.from([
  { id: "desk", label: "Desk", to: "/desk" },
  { id: "other", label: "Other", to: "/other" },
]);
const slots = [
  { slot: "form-view.sections", model: "notes.Note", id: "notes.fields" },
  { slot: "form-view.sections", model: "notes.Note", id: "notes.history" },
  { slot: "form-view.record-chrome", id: "iam.share" },
  { slot: "form-view.record-chrome", id: "workflow.run" },
  { slot: "console.notice", id: "preview.notice" },
];
const chatter = [{ id: "agents" }, { id: "tags" }];
const drawers = [{ id: "logs", edge: "bottom" as const, title: "Logs", render: () => null }];
const inventory = { slots, chatter, drawers };
const routes = [
  { name: "desk.home", path: "/desk" },
  { name: "desk.record", path: "/desk/$id", parent: "desk.home" },
  { name: "other.record", path: "/other/$id" },
];

describe("route admission", () => {
  test("app scope flows to children and a route narrows only named addresses", () => {
    const index = routePolicyIndex(routes, [
      { app: "desk", admit: { slots: { "form-view.record-chrome": ["iam.share", "workflow.run"] }, aside: ["comments", "agents"] }, chatter: { tabs: ["agents", "comments"] }, shell: { breadcrumb: false, commandSearch: true } },
      { app: "desk", route: "desk.record", admit: { slots: { "form-view.record-chrome": ["iam.share"], "form-view.sections": [] }, aside: ["comments"] }, chatter: { tabs: ["comments"] }, shell: { asideOpen: true } },
    ], menus, inventory);
    const record = index("desk", "desk.record");
    expect(index("desk", "desk.home").admit?.slots?.["form-view.record-chrome"]).toEqual(["iam.share", "workflow.run"]);
    expect(record.admit?.slots?.["form-view.record-chrome"]).toEqual(["iam.share"]);
    expect(record.admit?.slots?.["form-view.sections"]).toEqual([]);
    expect(record.admit?.aside).toEqual(["comments"]);
    expect(record.chatter).toEqual({ tabs: ["comments"] });
    expect(record.shell).toEqual({ breadcrumb: false, commandSearch: true, asideOpen: true });
    expect(admittedContributions(slots, record.admit, "slots").map((entry) => entry.id)).toEqual(["iam.share", "preview.notice"]);
    expect(admittedContributions(drawers, record.admit, "drawers")).toEqual(drawers);
  });

  test("confinement does not change omitted slots, aside or drawers", () => {
    const policy = routePolicyIndex(routes, [], menus, inventory)("desk", "desk.home");
    expect(admittedContributions(slots, policy.admit, "slots")).toEqual(slots);
    expect(admittedContributions(chatter, policy.admit, "aside")).toEqual(chatter);
    expect(admittedContributions(drawers, policy.admit, "drawers")).toEqual(drawers);
    expect(policy.admit).toBeUndefined();
  });

  test("a route scope belongs to its app, even when the route is owned elsewhere", () => {
    const index = routePolicyIndex(routes, [{ app: "desk", route: "other.record", admit: { slots: { "form-view.record-chrome": [] } } }], menus, inventory);
    expect(admittedContributions(slots, index("desk", "other.record").admit, "slots").map((entry) => entry.id))
      .toEqual(["notes.fields", "notes.history", "preview.notice"]);
    expect(index("other", "other.record").admit).toBeUndefined();
  });

  test("validates static ids, roots, routes and duplicate scopes", () => {
    expect(() => routePolicyIndex(routes, [{ app: "desk", admit: { slots: { "console.notice": ["missing"] } } }], menus, inventory))
      .toThrow('unknown slot contribution "console.notice:missing"');
    expect(() => routePolicyIndex(routes, [{ app: "missing" }], menus, inventory)).toThrow('unknown app "missing"');
    expect(() => routePolicyIndex(routes, [{ app: "desk", route: "missing" }], menus, inventory)).toThrow('unknown route "missing"');
    expect(() => routePolicyIndex(routes, [{ app: "desk" }, { app: "desk" }], menus, inventory)).toThrow('Surface scope "desk:*" is declared twice');
  });
});
