// @vitest-environment happy-dom
import { composeAddons, createApp, defineBaseAddon } from "@angee/app";
import { captureChrome, TEST_SCHEMAS } from "@angee/app/testing";
import { testDataResource } from "@angee/metadata/testing";
import messaging from "@angee/messaging";
import parties from "@angee/parties";
import { MenuTree } from "@angee/ui/chrome/menu-tree";
import { describe, expect, test } from "vitest";

import nexus from "./index";

// Minimal upstream menu contracts exercise aggregation without importing peer pages.
const upstream = [
  defineBaseAddon({ id: "messaging", menus: [{ id: "messaging", children: [
    { id: "messaging.messages", label: "Messages" },
    { id: "messaging.threads", label: "Threads" },
  ] }] }),
  defineBaseAddon({ id: "parties", menus: [{ id: "parties", children: [
    { id: "parties.people", label: "People" },
    { id: "parties.organizations", label: "Organizations" },
  ] }], containers: { "parties.overview#items": {} } }),
  defineBaseAddon({ id: "spaces", menus: [{ id: "spaces", label: "Spaces", route: "spaces.groups" }],
    routes: [{ name: "spaces.groups", path: "/spaces/groups", layout: "console" }] }),
  defineBaseAddon({ id: "posts", menus: [{ id: "posts", label: "Posts", route: "posts.feeds" }],
    routes: [{ name: "posts.feeds", path: "/posts/feeds", layout: "console" }] }),
];
const addons = [...upstream, { ...nexus, dependsOn: upstream.map(({ id }) => id) }];
const ownChildren = ["nexus.inbox", "nexus.graph", "nexus.ties", "nexus.cadences"];
const included = ["messaging", "parties", "spaces", "posts"];
const composed = composeAddons(addons, { canonicalModelLabel: (label) => label });
const logical = MenuTree.from(composed.menuComposition.logical);
const navigation = MenuTree.from(composed.menuComposition.navigation).withSettingsPlace();

describe("Nexus menu aggregation", () => {
  test("resolves its own destinations before the included apps in include order", () => {
    expect(logical.roots.map(({ id }) => id)).toEqual(["nexus"]);
    const root = logical.byId.get("nexus");
    // Route-less: the root lands on its first visible child, Inbox.
    expect(root?.route).toBeUndefined();
    expect(root?.children?.map(({ id }) => id)).toEqual([...ownChildren, ...included]);
    for (const id of ownChildren) {
      expect(logical.byId.get(id)?.parentNode?.id).toBe("nexus");
      expect(logical.byId.get("parties")?.children?.some((child) => child.id === id)).toBe(false);
    }
  });

  test("keeps each included app as its own group under Nexus", () => {
    expect(navigation.roots.map(({ id }) => id)).toEqual(["nexus"]);
    expect(navigation.byId.get("nexus")?.children?.map(({ id }) => id)).toEqual([...ownChildren, ...included]);
    for (const id of included) expect(navigation.byId.get(id)?.parentNode?.id).toBe("nexus");
    expect(navigation.byId.get("messaging")?.children?.map(({ id }) => id)).toEqual([
      "messaging.messages", "messaging.threads",
    ]);
    expect(navigation.byId.get("parties")?.children?.map(({ id }) => id)).toEqual([
      "parties.people", "parties.organizations",
    ]);
    expect(navigation.byId.get("spaces")?.route).toBe("spaces.groups");
    expect(navigation.byId.get("posts")?.route).toBe("posts.feeds");
  });

  test("explains aggregation at Nexus while keeping upstream declaration ownership", () => {
    const { explain } = createApp({
      addons,
      layouts: { console: { requireAuth: false } },
      schemas: { console: {
        url: "/graphql/console/",
        metadata: { angee: { resources: ["nexus.Tie", "nexus.Cadence", "parties.Party", "parties.Circle"].map((label) => testDataResource(label)) } },
      } },
      defaultSchema: "console",
    });
    expect(explain.menus.diagnostics).toEqual([]);
    expect(explain.menus.removed).toEqual([]);
    expect(explain.menus.hidden).toEqual([{ id: "nexus.ties", by: "nexus", reason: "hide" }]);
    expect(explain.menus.unavailable).toEqual({});
    for (const id of [...ownChildren, ...included]) {
      expect(explain.menus.provenance[id]?.parent).toBe("nexus");
    }
    for (const id of included) expect(explain.menus.provenance[id]?.flatten).toBeUndefined();
    expect(explain.menus.provenance.spaces?.route).toBe("spaces");
    expect(explain.menus.provenance.posts?.route).toBe("posts");
  });

  test.each([undefined, "nexus"])("composes the live rail, selected app's top bar and Settings (?app=%s)", async (app) => {
    // Compose the real Messaging and Parties declarations; the leaf contracts
    // above stand in for Spaces and Posts without importing undeclared web deps.
    const owners = [parties, { ...messaging, dependsOn: ["parties"] }, ...upstream.slice(2)];
    const liveAddons = [...owners, { ...nexus, dependsOn: owners.map(({ id }) => id) }];
    const models = [...new Set(liveAddons.flatMap((addon) => (addon.routes ?? []).flatMap((route) =>
      route.resource ? [route.resource] : [])))];
    const schemas = { ...TEST_SCHEMAS, console: {
      ...TEST_SCHEMAS.console,
      metadata: { angee: { resources: models.map((label) => testDataResource(label)) } },
    } };
    const { explain } = createApp({ addons: liveAddons, schemas, layouts: {
      console: { requireAuth: false }, public: { requireAuth: false, schema: "public" },
    }, defaultSchema: "console", location: { search: app ? `?app=${app}` : "" } });
    expect(explain.menus.diagnostics).toEqual([]);
    expect(explain.menus.unavailable).toEqual({});
    expect(explain.menus.provenance["messaging.channels"]).toMatchObject({ parent: "messaging", group: "messaging" });
    expect(explain.menus.provenance["parties.directories"]).toMatchObject({ parent: "parties", group: "parties" });
    expect(explain.selection.home).toBe(app ? "nexus.inbox" : undefined);
    // One mounted console per selection: the rail, top-bar selection and Settings
    // are facts of the composed menu tree, not of the path it was mounted on.
    const captured = await captureChrome({ addons: liveAddons, path: "/nexus/inbox", ...(app ? { app } : {}), schemas });
    try {
      const tree = MenuTree.from(captured.props().menus);
      const root = tree.railMenuItems()[0]!;
      expect(tree.railMenuItems().map((node) => node.displayLabel)).toEqual(["Nexus"]);
      expect(root.appChildren().map((node) => node.displayLabel)).toEqual(["Messaging", "Parties", "Spaces", "Posts"]);
      expect(root.target).toBe("/nexus/inbox");
      expect(tree.activeItem("/nexus/inbox")?.id).toBe("nexus.inbox");
      for (const [path, appId, labels] of [
        ["/nexus/inbox", "nexus", ["Inbox", "Graph", "Cadences"]],
        ["/messaging/messages", "messaging", ["Messages", "Threads"]],
        ["/parties/people", "parties", ["People", "Organizations", "Circles", "Review"]],
      ] as const) {
        const match = tree.match(path);
        expect(match?.app?.id).toBe(appId);
        expect(match?.app?.menuItems().map((node) => node.displayLabel)).toEqual(labels);
      }
      expect(tree.settingsMenuItems().map((node) => node.displayLabel)).toEqual(["Channels", "Contact directories"]);
      expect(tree.railPlace("/messaging/channels/one")).toMatchObject({ scope: "settings", activeRootId: "messaging.channels" });
      expect(tree.railPlace("/parties/directories/one")).toMatchObject({ scope: "settings", activeRootId: "parties.directories" });
      expect(tree.navigableItems().map(({ item }) => item.id)).toEqual(expect.arrayContaining([
        "nexus.ties", "parties.overview", "parties.relationships", "parties.handles",
      ]));
    } finally {
      captured.cleanup();
    }
  });
});
