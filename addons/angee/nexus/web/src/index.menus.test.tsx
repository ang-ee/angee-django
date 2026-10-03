// @vitest-environment happy-dom
import { composeAddons, createApp, defineBaseAddon } from "@angee/app";
import { testDataResource } from "@angee/metadata/testing";
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
const navigation = MenuTree.from(composed.menuComposition.navigation);

describe("Nexus menu aggregation", () => {
  test("resolves its own destinations before the included apps in include order", () => {
    expect(logical.roots.map(({ id }) => id)).toEqual(["nexus"]);
    const root = logical.byId.get("nexus");
    expect(root?.route).toBe("nexus.inbox");
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
    expect(explain.menus.hidden).toEqual([]);
    expect(explain.menus.unavailable).toEqual({});
    for (const id of [...ownChildren, ...included]) {
      expect(explain.menus.provenance[id]?.parent).toBe("nexus");
    }
    for (const id of included) expect(explain.menus.provenance[id]?.flatten).toBeUndefined();
    expect(explain.menus.provenance.spaces?.route).toBe("spaces");
    expect(explain.menus.provenance.posts?.route).toBe("posts");
  });
});
