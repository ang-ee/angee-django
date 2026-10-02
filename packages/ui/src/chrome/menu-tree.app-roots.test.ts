import { describe, expect, test } from "vitest";

import { MenuTree } from "./menu-tree";

describe("MenuTree.appRoots", () => {
  test("uses only explicitly opted-in roots when at least one opts in", () => {
    const tree = MenuTree.from([
      { id: "notes", to: "/notes", appRoot: true },
      { id: "archive", to: "/archive" },
      { id: "help", to: "/help", appRoot: false },
    ]);
    expect(tree.appRoots().map((root) => root.id)).toEqual(["notes"]);
    expect(tree.railMenuItems()).toEqual(tree.appRoots());
    expect(tree.byId.has("archive")).toBe(true);
  });

  test("keeps every root in the legacy fallback, including an explicit false", () => {
    const tree = MenuTree.from([
      { id: "notes", to: "/notes" }, { id: "help", to: "/help", appRoot: false },
    ]);
    expect(tree.appRoots()).toBe(tree.roots);
    expect(tree.appRoots().map((root) => root.id)).toEqual(["notes", "help"]);
  });

  test.each([true, false])("refuses appRoot=%s on a nested child", (appRoot) => {
    expect(() => MenuTree.from([{
      id: "notes", to: "/notes", children: [{ id: "archive", to: "/archive", appRoot }],
    }])).toThrow(/archive.*appRoot.*non-root/);
  });

  test("refuses appRoot on a child attached by parentId", () => {
    expect(() => MenuTree.from([
      { id: "notes", to: "/notes" },
      { id: "archive", to: "/archive", parentId: "notes", appRoot: true },
    ])).toThrow(/archive.*appRoot.*non-root/);
  });

  test("refuses a target-less app root", () => {
    expect(() => MenuTree.from([{ id: "empty", appRoot: true }]))
      .toThrow(/empty.*appRoot.*without a target/);
  });

  test("accepts a root whose target resolves through a child", () => {
    const tree = MenuTree.from([{
      id: "notes", appRoot: true, children: [{ id: "all", to: "/notes" }],
    }]);
    expect(tree.appRoots()[0]?.target).toBe("/notes");
    expect(tree.railMenuItems()).toEqual(tree.appRoots());
  });

  test("keeps platform roots available to Settings outside the app rail", () => {
    const tree = MenuTree.from([
      { id: "notes", to: "/notes", appRoot: true },
      { id: "platform", to: "/settings", group: "platform" },
    ]);
    expect(tree.railMenuItems().map((root) => root.id)).toEqual(["notes"]);
    expect(tree.settingsMenuItems().map((root) => root.id)).toEqual(["platform"]);
  });
});
