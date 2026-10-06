import { describe, expect, test } from "vitest";

import {
  ChromeMenuNode,
  MenuTree,
  pathMatchesTarget,
  resolveMenuRouteTargets,
  type ChromeMenuItem,
} from "./menu-tree";
import { createRouteHref } from "../runtime/route-href";

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

describe("match", () => {
  test("a declared anchor beats another item's path prefix; an absent anchor leaves the path alone", () => {
    const tree = MenuTree.from([{ id: "desk", children: [{ id: "desk.hub", to: "/desk/hub" }] },
      { id: "foreign", to: "/records" }]);
    expect(tree.match("/records/one")?.item.id).toBe("foreign");
    const match = tree.match("/records/one", undefined, false, "desk.hub");
    expect(match?.item.id).toBe("desk.hub");
    expect(match?.trail.map((item) => item.id)).toEqual(["desk", "desk.hub"]);
    expect(match?.app?.id).toBe("desk");
    expect(tree.match("/records/one", undefined, false, "removed")?.item.id).toBe("foreign");
  });

  test("a declared anchor in an app wins over a deeper Settings prefix; the Settings record stays there", () => {
    const navigation = MenuTree.from([{ id: "suite", children: [
      { id: "suite.queues", group: "platform", to: "/suite/queues" },
      { id: "suite.triage", to: "/suite/triage" },
    ] }]).withSettingsPlace();
    const triage = navigation.match("/suite/queues/q1/triage", undefined, false, "suite.triage");
    expect(triage?.item.id).toBe("suite.triage");
    expect(navigation.railPlace(triage)).toMatchObject({ scope: "apps", activeRootId: "suite" });
    // The path alone names the Settings page it nests under; the anchor is what keeps the page in its app.
    expect(navigation.railPlace(navigation.match("/suite/queues/q1/triage"))).toMatchObject({ scope: "settings" });
    expect(navigation.railPlace(navigation.match("/suite/queues/q1"))).toMatchObject({ scope: "settings", activeRootId: "suite.queues" });
  });

  test("the page's own destinations beat the anchor; depth decides among the items at or under it", () => {
    const tree = MenuTree.from([
      { id: "dashboards", to: "/dashboards" },
      { id: "accounting", children: [{ id: "vendors", children: [
        { id: "payable", to: "/dashboards/addon/accounts-payable" },
      ] }] },
      { id: "payments", to: "/payments", children: [
        { id: "vendor-payments", to: "/payments?preset=vendor" },
        { id: "foreign-payments", to: "/payments" },
      ] },
    ]);
    expect(tree.match("/dashboards/addon/accounts-payable", undefined, false, "dashboards")?.item.id).toBe("payable");
    expect(tree.match("/payments", undefined, false, "payments")?.item.id).toBe("foreign-payments");
    expect(tree.match("/payments?preset=vendor", undefined, false, "payments")?.item.id).toBe("vendor-payments");
    expect(tree.match("/payments?preset=other", undefined, false, "vendor-payments")?.item.id).toBe("foreign-payments");
  });

  test("an anchor on an app root keeps the page on its own item under that root", () => {
    const tree = MenuTree.from([
      { id: "files", to: "/storage", children: [{ id: "files.all", to: "/storage" }] },
      { id: "desk", children: [{ id: "desk.tools", children: [{ id: "desk.files", to: "/storage" }] }] },
    ]);
    expect(tree.match("/storage")?.item.id).toBe("desk.files");
    expect(tree.match("/storage", undefined, false, "files")?.item.id).toBe("files.all");
    expect(tree.match("/storage/one", undefined, false, "files")?.trail.map((item) => item.id)).toEqual(["files", "files.all"]);
  });

  test("anchored hidden apps use the same developer-mode visibility rule", () => {
    const tree = MenuTree.from([{ id: "suite", children: [
      { id: "desk", app: true, hidden: true, children: [{ id: "desk.page", to: "/desk" }] },
    ] }]);
    expect(tree.match("/desk", undefined, false, "desk.page")?.app?.id).toBe("suite");
    expect(tree.match("/desk", undefined, true, "desk.page")?.app?.id).toBe("desk");
  });

  test.each([
    ["", "m.all"],
    ["?preset=archived", "m.all"],
    ["?preset=unread", "m.unread"],
  ])("ranks mismatched preset references below the general page on %s", (search, expected) => {
    const tree = MenuTree.from([{ id: "m", children: [
      { id: "m.unread", to: "/m/messages?preset=unread" },
      { id: "m.all", to: "/m/messages" },
    ] }]);
    expect(tree.match("/m/messages", search)?.item.id).toBe(expected);
    expect(tree.match(`/m/messages${search}`)?.item.id).toBe(expected);
    expect(tree.roots[0]?.activeTargetedChild(`/m/messages${search}`)?.id).toBe(expected);
  });

  test("eight equal route references highlight the first declaring owner", () => {
    const tree = MenuTree.from(Array.from({ length: 8 }, (_, index) => ({
      id: `owner-${index}`, to: "/shared",
    })));
    expect(tree.match("/shared")?.item.id).toBe("owner-0");
    expect(tree.activeItem("/shared")?.id).toBe("owner-0");
    expect(tree.activeAppRoot("/shared")?.id).toBe("owner-0");
  });

  test("a child beats its root on the same path and returns its trail", () => {
    const tree = MenuTree.from([{ id: "desk", to: "/desk", children: [
      { id: "desk.home", to: "/desk" },
    ] }]);
    expect(tree.match("/desk")?.item.id).toBe("desk.home");
    expect(tree.match("/desk")?.trail.map((item) => item.id)).toEqual(["desk", "desk.home"]);
    expect(tree.roots[0]?.activeTargetedChild("/desk")?.id).toBe("desk.home");
  });

  test("more equal preset search params win before depth, while path length wins first", () => {
    const tree = MenuTree.from([{ id: "desk", children: [
      { id: "desk.all", to: "/desk/notes" },
      { id: "desk.open", to: "/desk/notes?preset=open", children: [
        { id: "desk.deep", to: "/desk/notes" },
      ] },
      { id: "desk.mine", to: "/desk/notes?preset=open&owner=me" },
      { id: "desk.record", to: "/desk/notes/one" },
    ] }]);
    expect(tree.match("/desk/notes", "?preset=open")?.item.id).toBe("desk.open");
    expect(tree.match("/desk/notes?preset=open&owner=me")?.item.id).toBe("desk.mine");
    expect(tree.match("/desk/notes/one", "?preset=open&owner=me")?.item.id).toBe("desk.record");
    expect(tree.match("/unrelated")).toBeUndefined();
  });

  test("pre-order follows the assembled tree, including parentId contributions", () => {
    const tree = MenuTree.from([
      { id: "contribution", parentId: "desk", to: "/shared" },
      { id: "desk", children: [{ id: "desk.owner", to: "/shared" }] },
    ]);
    expect(tree.activeItem("/shared")?.id).toBe("desk.owner");
  });

  test("nodes separate apps and menus and select the nearest visible app", () => {
    const tree = MenuTree.from([{ id: "suite", children: [
      { id: "desk", app: true, children: [{ id: "desk.notes", to: "/notes" }] },
      { id: "suite.inbox", to: "/inbox" },
      { id: "hidden", app: true, hidden: true, to: "/hidden" },
    ] }, { id: "legacy", parentId: "suite", to: "/legacy" },
    { id: "platform", group: "platform", to: "/settings" }]);
    const suite = tree.byId.get("suite")!;
    expect(suite.isApp).toBe(true);
    expect(suite.appChildren().map((item) => item.id)).toEqual(["desk"]);
    expect(suite.menuItems().map((item) => item.id)).toEqual(["suite.inbox", "legacy"]);
    expect(tree.byId.get("platform")?.isApp).toBe(false);
    expect(tree.match("/notes")?.app?.id).toBe("desk");
    expect(tree.match("/hidden")?.app?.id).toBe("suite");
  });
});

test("confinement selects one root for the rail and palette without mutating composition", () => {
  const tree = MenuTree.from([...MENU, { id: "notes.extra", parentId: "notes", to: "/notes/extra" }]);
  const confined = tree.confineTo(["notes"]);
  expect(confined.railMenuItems().map((node) => node.id)).toEqual(["notes"]);
  expect(confined.navigableItems().map(({ item }) => item.id)).toEqual(["notes.all", "notes.archive", "notes.extra"]);
  expect(confined.settingsEntry()).toBeUndefined();
  expect(tree.roots).toHaveLength(3);
  expect(() => tree.confineTo(["missing"])).toThrow(/Unknown menu root "missing"/);
  expect(() => tree.confineTo(["notes", "notes.all"])).toThrow(/Unknown menu root "notes.all"/);
});

test("a rail of several roots keeps them in the rail's order, a Settings root among them as an app", () => {
  const tree = MenuTree.from([...MENU,
    { id: "platform", label: "Platform", group: "platform", to: "/settings/platform" },
    { id: "appearance", label: "Appearance", group: "platform", personal: true, to: "/settings/appearance" }]);
  const rail = tree.confineTo(["single", "platform", "notes"]);
  expect(rail.railMenuItems().map((node) => node.id)).toEqual(["single", "platform", "notes"]);
  expect(rail.byId.get("platform")?.isApp).toBe(true);
  expect(rail.settingsMenuItems().map((node) => node.id)).toEqual(["appearance"]);
  expect(rail.byId.has("operator")).toBe(false);
  expect(rail.activeAppRoot("/operator/services")).toBeUndefined();
});

test("confinement keeps personal Settings roots, such as appearance, beside the root's own", () => {
  const tree = MenuTree.from([...MENU,
    { id: "appearance", label: "Appearance", group: "platform", personal: true, to: "/settings/appearance" },
    { id: "platform", label: "Platform", group: "platform", to: "/settings/platform" },
    { id: "notes.settings", parentId: "notes", label: "Notes settings", group: "platform", to: "/notes/settings" }]);
  const confined = tree.confineTo(["notes"]);
  expect(confined.settingsMenuItems().map((node) => node.id)).toEqual(["notes.settings", "appearance"]);
  expect(confined.railMenuItems().map((node) => node.id)).toEqual(["notes"]);
});

test.each([false, true])("lifts nested platform nodes into Settings (confined=%s)", (confined) => {
  const logical = MenuTree.from([
    { id: "suite", children: [
      { id: "suite.inbox", to: "/suite/inbox" },
      { id: "mail", app: true, children: [
        { id: "mail.messages", to: "/mail/messages" },
        { id: "mail.settings", group: "platform", children: [
          { id: "mail.channels", to: "/mail/channels" },
        ] },
        { id: "mail.hidden-settings", to: "/mail/hidden", group: "platform", hidden: true },
      ] },
      { id: "configuration", app: true, group: "platform", to: "/configuration" },
    ] },
    { id: "appearance", group: "platform", personal: true, to: "/appearance" },
  ]);
  const navigation = confined ? logical.confineTo(["suite"]) : logical.withSettingsPlace();
  expect(navigation.railMenuItems().map((node) => node.id)).toEqual(["suite"]);
  expect(navigation.byId.get("suite")?.appChildren().map((node) => node.id)).toEqual(["mail"]);
  expect(navigation.byId.get("mail")?.menuItems().map((node) => node.id)).toEqual(["mail.messages"]);
  expect(navigation.settingsMenuItems().map((node) => node.id)).toEqual(["mail.settings", "configuration", "appearance"]);
  expect(navigation.settingsMenuItems(true).map((node) => node.id)).toContain("mail.hidden-settings");
  expect(navigation.byId.get("configuration")?.isApp).toBe(false);
  expect(navigation.railPlace("/mail/channels/one")).toMatchObject({ scope: "settings", activeRootId: "mail.settings" });
  expect(navigation.match("/mail/channels/one")?.app).toBeUndefined();
  expect(navigation.navigableItems().map(({ item }) => item.id)).toContain("mail.hidden-settings");
  expect(navigation.trailFor("mail.channels").map((node) => node.id)).toEqual(["mail.settings", "mail.channels"]);
  expect(logical.trailFor("mail.channels").map((node) => node.id)).toEqual(["suite", "mail", "mail.settings", "mail.channels"]);
  expect(logical.byId.get("mail.settings")?.parentNode?.id).toBe("mail");
});

test("without drops nodes with their subtrees, and any node left with nowhere to go", () => {
  const tree = MenuTree.from([
    { id: "desk", children: [
      { id: "desk.tools", children: [{ id: "desk.export", to: "/desk/export" }] },
      { id: "desk.inbox", to: "/desk/inbox" },
    ] },
    { id: "audit", children: [{ id: "audit.log", to: "/audit/log" }] },
  ]);
  expect(tree.without(new Set())).toBe(tree);
  // "desk.tools" reached only "desk.export"; the root "audit" reached only "audit.log".
  const left = tree.without(new Set(["desk.export", "audit.log"]));
  expect([...left.byId.keys()]).toEqual(["desk", "desk.inbox"]);
  expect(left.railMenuItems().map((node) => node.id)).toEqual(["desk"]);
  expect(left.byId.get("desk")?.target).toBe("/desk/inbox");
  expect(tree.byId.size).toBe(6);
});

test("withSettingsPlace is idempotent, retaining platform descendants inside Settings roots", () => {
  const tree = MenuTree.from([{ id: "mail", children: [
    { id: "mail.settings", group: "platform", children: [
      { id: "mail.channels", group: "platform", to: "/mail/channels" },
    ] },
  ] }]);
  const once = tree.withSettingsPlace();
  expect(once.withSettingsPlace()).toEqual(once);
});

describe("resolveMenuRouteTargets", () => {
  const routeHref = createRouteHref([
    { name: "dashboards.addon", path: "/dashboards/addon/$key" },
  ]);

  test("resolves parameterized route targets through the route href owner", () => {
    expect(
      resolveMenuRouteTargets(
        [
          {
            id: "review-queue",
            route: "dashboards.addon",
            params: { key: "example.document_review/review queue" },
          },
        ],
        routeHref,
      ),
    ).toEqual([
      {
        id: "review-queue",
        route: "dashboards.addon",
        params: { key: "example.document_review/review queue" },
        to: "/dashboards/addon/example.document_review%2Freview%20queue",
        children: undefined,
      },
    ]);
  });

  test("keeps to for external links only", () => {
    expect(
      resolveMenuRouteTargets(
        [{ id: "docs", to: "https://docs.example.test/start" }],
        routeHref,
      )[0]?.to,
    ).toBe("https://docs.example.test/start");
    expect(() =>
      resolveMenuRouteTargets(
        [{ id: "literal", to: "/dashboards/addon/literal" }],
        routeHref,
      ),
    ).toThrow(/declares internal target.*use route and params/);
  });

  test("menu view defaults are URL intent and never change path specificity", () => {
    const tree = MenuTree.from(resolveMenuRouteTargets([{
      id: "desk", route: "desk.home", defaultResourceView: "desk.a-long-preset-name", children: [
        { id: "desk.notes", route: "desk.notes" },
      ],
    }], createRouteHref([
      { name: "desk.home", path: "/desk" },
      { name: "desk.notes", path: "/desk/notes" },
    ])));
    expect(tree.roots[0]?.target).toBe("/desk?preset=desk.a-long-preset-name");
    expect(tree.activeItem("/desk/notes/r1")?.id).toBe("desk.notes");
    expect(tree.activeItem("/desk")?.id).toBe("desk");
  });

  test("rejects params without their route owner", () => {
    expect(() =>
      resolveMenuRouteTargets(
        [{ id: "orphan", params: { key: "value" } }],
        routeHref,
      ),
    ).toThrow(/declares params without a route/);
  });
});

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
  test("lists every root", () => {
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
      { id: "notes", label: "Notes", to: "/notes" },
      {
        id: "iam",
        label: "Permissions",
        group: "platform",
        children: [{ id: "iam.users", to: "/iam/users" }],
      },
      { id: "files", label: "Files", to: "/files" },
      {
        id: "platform",
        label: "Platform",
        group: "platform",
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
    expect(tree.railPlace("/platform/models/Note").scope).toBe("settings");
    expect(tree.railPlace("/notes").scope).toBe("apps");
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

  test("throws when target fallback links cycle", () => {
    const a = new ChromeMenuNode({ id: "a" });
    const b = new ChromeMenuNode({ id: "b" });
    a.appendChild(b);
    b.appendChild(a);

    expect(() => a.target).toThrow(/Menu item "a" creates a target cycle/);
  });
});
