import { describe, expect, test } from "vitest";

import { ChromeMenuNode, MenuTree, type ChromeMenuItem } from "./menu-tree";

// Two apps with sections plus a single-page app. The top bar navigates within
// the active app, so it shows that app's sections and never a sibling's.
const MENU: readonly ChromeMenuItem[] = [
  {
    id: "notes",
    label: "Notes",
    to: "/notes",
    children: [
      { id: "notes.all", label: "All", to: "/notes" },
      { id: "notes.archive", label: "Archived", to: "/notes/archive" },
    ],
  },
  {
    id: "operator",
    label: "Operator",
    icon: "operator",
    children: [
      { id: "operator.overview", label: "Overview", to: "/operator" },
      { id: "operator.services", label: "Services", to: "/operator/services" },
    ],
  },
  { id: "single", label: "Single", to: "/single" },
];

describe("appSectionItems", () => {
  test("returns the active app's sections, flat", () => {
    expect(MenuTree.from(MENU).appSectionItems("/operator/services").map((item) => item.id)).toEqual([
      "operator.overview",
      "operator.services",
    ]);
  });

  test("scopes to the app the path belongs to — a sibling app never leaks", () => {
    expect(MenuTree.from(MENU).appSectionItems("/notes").map((item) => item.id)).toEqual([
      "notes.all",
      "notes.archive",
    ]);
  });

  test("matches the app from a nested section path", () => {
    expect(MenuTree.from(MENU).appSectionItems("/notes/archive").map((item) => item.id)).toEqual([
      "notes.all",
      "notes.archive",
    ]);
  });

  test("a single-page app (no children) contributes nothing", () => {
    expect(MenuTree.from(MENU).appSectionItems("/single")).toEqual([]);
  });

  test("an unmatched path yields no sections", () => {
    expect(MenuTree.from(MENU).appSectionItems("/nope")).toEqual([]);
  });
});

describe("trailFor", () => {
  test("walks nested and parent-linked ancestors", () => {
    const tree = MenuTree.from([
      {
        id: "identity",
        label: "Identity",
        children: [
          {
            id: "identity.users",
            label: "Users",
            route: "iam.users",
            to: "/iam/users",
          },
        ],
      },
      {
        id: "identity.roles",
        label: "Roles",
        parentId: "identity",
        route: "iam.roles",
        to: "/iam/roles",
      },
    ]);

    expect(tree.trailFor("identity.users").map((item) => item.id)).toEqual([
      "identity",
      "identity.users",
    ]);
    expect(tree.trailFor("identity.roles").map((item) => item.id)).toEqual([
      "identity",
      "identity.roles",
    ]);
  });

  test("indexes route references", () => {
    const tree = MenuTree.from(MENU);

    expect(tree.itemsForRoute("missing")).toEqual([]);
    expect(
      MenuTree.from([
        { id: "a", route: "shared", to: "/shared" },
        { id: "b", route: "shared", to: "/shared-alt" },
      ]).itemsForRoute("shared").map((item) => item.id),
    ).toEqual(["a", "b"]);
  });

  test("throws when a direct caller provides duplicate ids", () => {
    expect(() =>
      MenuTree.from([
        { id: "dup", route: "a", to: "/a" },
        { id: "dup", route: "b", to: "/b" },
      ]),
    ).toThrow(/Menu item "dup" is declared more than once/);
  });

  test("throws when parent links cycle", () => {
    expect(() =>
      MenuTree.from([
        { id: "a", parentId: "b" },
        { id: "b", parentId: "a" },
      ]),
    ).toThrow(/Menu item "a" creates a parent cycle/);
  });

  test("throws when target fallback links cycle", () => {
    const a = new ChromeMenuNode({ id: "a" });
    const b = new ChromeMenuNode({ id: "b" });
    a.appendChild(b);
    b.appendChild(a);

    expect(() => a.target).toThrow(/Menu item "a" creates a target cycle/);
  });
});
