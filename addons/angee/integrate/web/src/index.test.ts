import { MenuTree, type BaseMenuItem, type ChromeMenuItem } from "@angee/base";
import { describe, expect, test } from "vitest";

import integrate from "./index";

describe("integrate addon manifest", () => {
  test("registers the integrations landing on the console shell with a component", () => {
    const integrations = (integrate.routes ?? []).find(
      (route) => route.name === "integrate.integrations",
    );
    expect(integrations?.path).toBe("/integrate");
    expect(integrations?.shell).toBe("console");
    expect(integrations?.component).toBeTypeOf("function");
    // No `menu:` — the route-less root no longer references this route, so a
    // `menu` would mismatch (createApp throws "item does not reference the route").
    expect(integrations?.menu).toBeUndefined();
  });

  test("nests each record route under its list, no component", () => {
    for (const [name, parent] of [
      ["integrate.integration", "integrate.integrations"],
      ["integrate.vendor", "integrate.vendors"],
      ["integrate.webhook", "integrate.webhooks"],
      ["integrate.vcsIntegration", "integrate.vcs"],
      ["integrate.repository", "integrate.repositories"],
      ["integrate.source", "integrate.sources"],
    ] as const) {
      const record = (integrate.routes ?? []).find((route) => route.name === name);
      expect(record?.path).toContain("/$id");
      expect(record?.parent).toBe(parent);
      expect(record?.component).toBeUndefined();
    }
  });

  test("keeps the static list routes as siblings, not integration ids", () => {
    for (const [name, path] of [
      ["integrate.vendors", "/integrate/vendors"],
      ["integrate.webhooks", "/integrate/webhooks"],
      ["integrate.vcs", "/integrate/vcs"],
      ["integrate.repositories", "/integrate/repositories"],
      ["integrate.sources", "/integrate/sources"],
    ] as const) {
      const route = (integrate.routes ?? []).find((entry) => entry.name === name);
      expect(route?.path).toBe(path);
      expect(route?.component).toBeTypeOf("function");
    }
  });

  test("exposes an Integrations menu with every list as a child", () => {
    expect(integrate.menus).toHaveLength(1);
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("integrate");
    // Route-less root: target inherited from the first child (Integrations).
    expect(menu?.route).toBeUndefined();
    expect(menu?.group).toBe("platform");
    expect(menu?.children?.map((child) => child.id)).toEqual([
      "integrate.integrations",
      "integrate.vendors",
      "integrate.webhooks",
      "integrate.vcs",
      "integrate.repositories",
      "integrate.sources",
      "integrate.connections",
    ]);
  });

  test("groups the outbound connect surface under a route-less Connections dropdown", () => {
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    const connections = menu?.children?.find(
      (child) => child.id === "integrate.connections",
    );
    // Route-less group → renders as a dropdown of its children.
    expect(connections?.route).toBeUndefined();
    expect(connections?.children?.map((child) => child.route)).toEqual([
      "integrate.providers",
      "integrate.accounts",
      "integrate.credentials",
    ]);
  });

  test("registers the account-connect callback on the console shell", () => {
    const route = (integrate.routes ?? []).find(
      (item) => item.name === "integrate.connect.callback",
    );
    const legacy = (integrate.routes ?? []).find(
      (item) => item.name === "integrate.connect.callback.legacy",
    );
    expect(route?.path).toBe("/callback");
    expect(route?.shell).toBe("console");
    expect(route?.component).toBeTypeOf("function");
    expect(legacy?.path).toBe("/iam/oauth/callback");
    expect(legacy?.component).toBe(route?.component);
  });

  test("nests each connect record route under its list, no component", () => {
    for (const [name, parent] of [
      ["integrate.provider", "integrate.providers"],
      ["integrate.account", "integrate.accounts"],
      ["integrate.credential", "integrate.credentials"],
    ] as const) {
      const record = (integrate.routes ?? []).find((route) => route.name === name);
      expect(record?.path).toContain("/$id");
      expect(record?.parent).toBe(parent);
      expect(record?.component).toBeUndefined();
    }
  });

  test("registers the Credential create form override", () => {
    expect(integrate.forms?.Credential).toBeDefined();
  });

  test("references the landing route from exactly one menu item (chrome derivation)", () => {
    const tree = MenuTree.from(integrate.menus as readonly ChromeMenuItem[]);
    expect(tree.itemsForRoute("integrate.integrations")).toHaveLength(1);
  });

  test("registers its glyphs", () => {
    for (const name of [
      "integrate",
      "integration",
      "vendor",
      "webhook",
      "vcs",
      "repository",
      "source",
    ] as const) {
      expect(integrate.icons?.[name]).toBeDefined();
    }
  });
});
