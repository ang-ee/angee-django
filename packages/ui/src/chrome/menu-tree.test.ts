import { describe, expect, test } from "vitest";

import {
  ChromeMenuNode,
  MenuTree,
  pathMatchesTarget,
  type ChromeMenuItem,
} from "./menu-tree";

// Two apps with sections plus a single-page app.
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

describe("navigableItems", () => {
  test("returns navigable leaves paired with their root app, in build order", () => {
    expect(
      MenuTree.from(MENU)
        .navigableItems()
        .map(({ item, root, target }) => ({ id: item.id, root: root.id, target })),
    ).toEqual([
      { id: "notes.all", root: "notes", target: "/notes" },
      { id: "notes.archive", root: "notes", target: "/notes/archive" },
      { id: "operator.overview", root: "operator", target: "/operator" },
      { id: "operator.services", root: "operator", target: "/operator/services" },
      { id: "single", root: "single", target: "/single" },
    ]);
  });

  test("excludes a parent that only borrows a child's target — the leaf carries it", () => {
    const ids = MenuTree.from(MENU)
      .navigableItems()
      .map(({ item }) => item.id);
    // `operator` resolves /operator from its first child; `notes` has its own
    // `to` but also children — both are parents, so their leaves carry targets.
    expect(ids).not.toContain("operator");
    expect(ids).not.toContain("notes");
  });

  test("excludes the chrome action menus (systray/user) and their entries", () => {
    const ids = MenuTree.from([
      { id: "notes", label: "Notes", to: "/notes" },
      {
        id: "user",
        label: "User",
        children: [{ id: "user.profile", label: "Profile", to: "/profile" }],
      },
      {
        id: "systray",
        label: "Systray",
        children: [{ id: "systray.help", label: "Help", to: "/help" }],
      },
    ])
      .navigableItems()
      .map(({ item }) => item.id);
    expect(ids).toEqual(["notes"]);
  });

  test("skips entries with no target or a '#' placeholder", () => {
    const ids = MenuTree.from([
      { id: "real", label: "Real", to: "/real" },
      { id: "placeholder", label: "Placeholder", to: "#" },
      { id: "labelOnly", label: "Label only" },
    ])
      .navigableItems()
      .map(({ item }) => item.id);
    expect(ids).toEqual(["real"]);
  });

  test("keeps Settings-category leaves available to the command palette", () => {
    expect(
      MenuTree.from([
        {
          id: "agents.ai",
          label: "AI",
          group: "platform",
          children: [
            { id: "agents.providers", label: "Providers", to: "/agents/providers" },
            { id: "agents.models", label: "Models", to: "/agents/models" },
          ],
        },
      ])
        .navigableItems()
        .map(({ item, root, target }) => ({ id: item.id, root: root.id, target })),
    ).toEqual([
      { id: "agents.providers", root: "agents.ai", target: "/agents/providers" },
      { id: "agents.models", root: "agents.ai", target: "/agents/models" },
    ]);
  });
});

describe("railMenuItems", () => {
  test("filters to authored app roots when refine projection marks them", () => {
    const ids = MenuTree.from([
      { id: "agents", label: "Agents", to: "/agents", appRoot: true },
      { id: "agents.menu", label: "Agents", to: "/agents" },
      { id: "agents.list", label: "Agents", to: "/agents" },
      { id: "notes", label: "Notes", to: "/notes", appRoot: true },
    ])
      .railMenuItems()
      .map((item) => item.id);

    expect(ids).toEqual(["agents", "notes"]);
  });

  test("keeps legacy direct menu fixtures when no app root markers exist", () => {
    const ids = MenuTree.from([
      { id: "agents", label: "Agents", to: "/agents" },
      { id: "notes", label: "Notes", to: "/notes" },
    ])
      .railMenuItems()
      .map((item) => item.id);

    expect(ids).toEqual(["agents", "notes"]);
  });

  test("separates platform roots into the Settings place in declaration order", () => {
    const tree = MenuTree.from([
      { id: "notes", label: "Notes", to: "/notes", appRoot: true },
      {
        id: "iam",
        label: "Permissions",
        group: "platform",
        appRoot: true,
        children: [{ id: "iam.users", to: "/iam/users" }],
      },
      { id: "files", label: "Files", to: "/files", appRoot: true },
      {
        id: "platform",
        label: "Platform",
        group: "platform",
        appRoot: true,
        children: [{ id: "platform.models", to: "/platform/models" }],
      },
    ]);

    expect(tree.railMenuItems().map((item) => item.id)).toEqual([
      "notes",
      "files",
    ]);
    expect(tree.settingsMenuItems().map((item) => item.id)).toEqual([
      "iam",
      "platform",
    ]);
    expect(tree.settingsEntry()).toMatchObject({
      id: "settings",
      icon: "settings",
      target: "/iam/users",
      group: "platform",
    });
    expect(tree.settingsEntry()?.items.map((item) => item.id)).toEqual([
      "iam",
      "platform",
    ]);
    expect(tree.isSettingsActive("/platform/models/Note")).toBe(true);
    expect(tree.isSettingsActive("/notes")).toBe(false);
  });
});

describe("active branches and pathMatchesTarget", () => {
  test("pathMatchesTarget matches an exact or nested path, never missing/#", () => {
    expect(pathMatchesTarget("/notes", "/notes")).toBe(true);
    expect(pathMatchesTarget("/notes/archive", "/notes")).toBe(true);
    expect(pathMatchesTarget("/notebooks", "/notes")).toBe(false); // segment-aware
    expect(pathMatchesTarget("/x", "#")).toBe(false);
    expect(pathMatchesTarget("/x", undefined)).toBe(false);
  });

  test("finds the most-specific active child through nested descendants", () => {
    const tree = MenuTree.from([
      {
        id: "agents",
        to: "/agents",
        children: [
          { id: "agents.all", to: "/agents" },
          {
            id: "agents.skills",
            children: [
              { id: "agents.skills.all", to: "/agents/skills" },
              { id: "agents.skills.sources", to: "/agents/skills/sources" },
            ],
          },
        ],
      },
    ]);
    const agents = tree.byId.get("agents");

    expect(agents?.activeTargetedChild("/agents/skills/sources/one")?.id)
      .toBe("agents.skills");
    expect(tree.activeAppRoot("/agents/skills/sources/one")?.id).toBe("agents");
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

  test("reserves the synthetic Settings place id", () => {
    expect(() => MenuTree.from([{ id: "settings", to: "/settings" }]))
      .toThrow(/reserved Settings place id/);
  });

  test("throws when parent links cycle", () => {
    expect(() =>
      MenuTree.from([
        { id: "a", parentId: "b" },
        { id: "b", parentId: "a" },
      ]),
    ).toThrow(/Menu item "a" creates a parent cycle/);
  });

  test("throws when a contribution names an unknown parent", () => {
    expect(() =>
      MenuTree.from([{ id: "child", parentId: "ghost" }]),
    ).toThrow(/Menu item "child" names unknown parent "ghost"/);
  });

  test("tolerates a reserved virtual parent (systray/user) with no node", () => {
    expect(() =>
      MenuTree.from([{ id: "entry", parentId: "systray", to: "/x" }]),
    ).not.toThrow();
  });

  test("throws when target fallback links cycle", () => {
    const a = new ChromeMenuNode({ id: "a" });
    const b = new ChromeMenuNode({ id: "b" });
    a.appendChild(b);
    b.appendChild(a);

    expect(() => a.target).toThrow(/Menu item "a" creates a target cycle/);
  });
});
