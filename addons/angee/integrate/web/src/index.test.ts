import { MenuTree, type BaseMenuItem, type ChromeMenuItem } from "@angee/ui";
import { describe, expect, test } from "vitest";

import integrate from "./index";

describe("integrate addon manifest", () => {
  test("registers the integrations landing on the console layout with a component", () => {
    const integrations = (integrate.routes ?? []).find(
      (route) => route.name === "integrate.integrations",
    );
    expect(integrations?.path).toBe("/integrate");
    expect(integrations?.layout).toBe("console");
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
      ["integrate.vcsBridge", "integrate.vcs"],
      ["integrate.repository", "integrate.repositories"],
      ["integrate.source", "integrate.sources"],
      ["integrate.template", "integrate.templates"],
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
      ["integrate.templates", "/integrate/templates"],
    ] as const) {
      const route = (integrate.routes ?? []).find((entry) => entry.name === name);
      expect(route?.path).toBe(path);
      expect(route?.component).toBeTypeOf("function");
    }
  });

  test("exposes an Integrations menu grouped by integration, source, OAuth, and credentials concern", () => {
    expect(integrate.menus).toHaveLength(1);
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("integrate");
    // Route-less root: target inherited from the first child (Integrations).
    expect(menu?.route).toBeUndefined();
    expect(menu?.group).toBe("platform");
    expect(menu?.children?.map((child) => child.id)).toEqual([
      "integrate.integrations.group",
      "integrate.sources.group",
      "integrate.oauth.group",
      "integrate.credentials",
    ]);
  });

  test("groups integration records with vendors and webhooks", () => {
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    const group = menu?.children?.find(
      (child) => child.id === "integrate.integrations.group",
    );
    expect(group?.label).toBe("Integrations");
    expect(group?.route).toBeUndefined();
    expect(group?.children?.map((child) => child.id)).toEqual([
      "integrate.integrations",
      "integrate.vendors",
      "integrate.webhooks",
    ]);
  });

  test("groups repository inventory under Sources with VCS bridges", () => {
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    const sources = menu?.children?.find(
      (child) => child.id === "integrate.sources.group",
    );
    expect(sources?.label).toBe("Sources");
    expect(sources?.route).toBeUndefined();
    expect(sources?.children?.map((child) => [child.label, child.route])).toEqual([
      ["Sources", "integrate.sources"],
      ["Templates", "integrate.templates"],
      ["Repositories", "integrate.repositories"],
      ["VCS Bridges", "integrate.vcs"],
    ]);
  });

  test("groups OAuth setup with external accounts", () => {
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    const oauth = menu?.children?.find(
      (child) => child.id === "integrate.oauth.group",
    );
    expect(oauth?.label).toBe("OAuth");
    expect(oauth?.route).toBeUndefined();
    expect(oauth?.children?.map((child) => child.route)).toEqual([
      "integrate.providers",
      "integrate.accounts",
    ]);
  });

  test("keeps credentials as a top-level integration section", () => {
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    const credentials = menu?.children?.find(
      (child) => child.id === "integrate.credentials",
    );
    expect(credentials?.label).toBe("Credentials");
    expect(credentials?.route).toBe("integrate.credentials");
    expect(credentials?.children).toBeUndefined();
  });

  test("registers the account-connect callback on the console layout", () => {
    const route = (integrate.routes ?? []).find(
      (item) => item.name === "integrate.connect.callback",
    );
    expect(route?.path).toBe("/integrate/oauth/callback");
    expect(route?.layout).toBe("console");
    expect(route?.component).toBeTypeOf("function");
  });

  test("mounts the loopback `/callback` alias for fixed public clients", () => {
    const loopback = (integrate.routes ?? []).find(
      (item) => item.name === "integrate.connect.callbackLoopback",
    );
    // Pinned to `/callback` to match the seeded `loopback_redirect_path` (see
    // tests/test_connections.py) — drift on either side fails a test, not a connect.
    expect(loopback?.path).toBe("/callback");
    expect(loopback?.layout).toBe("console");
    expect(loopback?.component).toBeTypeOf("function");
    // The legacy IAM login callback alias stays dropped; this restores the connect loopback only.
    expect(
      (integrate.routes ?? []).some((item) => item.path === "/iam/oauth/callback"),
    ).toBe(false);
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
      "integrate-template",
    ] as const) {
      expect(integrate.icons?.[name]).toBeDefined();
    }
  });
});
