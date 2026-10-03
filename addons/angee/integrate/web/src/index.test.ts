import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import {
  MenuTree,
  resolveContainer,
  type BaseMenuItem,
  type ChromeMenuItem,
} from "@angee/ui";
import { describe, expect, test } from "vitest";

import integrate from "./index";
import { INTEGRATION_MODEL } from "./IntegrationLifecycleActions";
import { INTEGRATION_STREAMS_TAB_ID } from "./IntegrationStreams";

const composed = composeAddons([integrate], { canonicalModelLabel: (model) => model }).containers;
/** The child ids one form container resolves for a subtype record. */
const childIds = (address: string, models: readonly string[]) =>
  resolveContainer(composed, address, { models }).map((child) => child.id);

describe("integrate addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(integrate)).not.toThrow();
  });

  test("registers the integrations landing on the console layout with a component", () => {
    const integrations = (integrate.routes ?? []).find(
      (route) => route.name === "integrate.integrations",
    );
    expect(integrations?.path).toBe("/integrate");
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
    ] as const) {
      const route = (integrate.routes ?? []).find((entry) => entry.name === name);
      expect(route?.path).toBe(path);
      expect(route?.component).toBeTypeOf("function");
    }
  });

  test("exposes an Integrations menu grouped by integration, OAuth, and credentials concern", () => {
    expect(integrate.menus).toHaveLength(1);
    const menu = integrate.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("integrate");
    // Route-less root: target inherited from the first child (Integrations).
    expect(menu?.route).toBeUndefined();
    expect(menu?.group).toBeUndefined();
    expect(menu?.icon).toBe("integration");
    expect(menu?.children?.map((child) => child.id)).toEqual([
      "integrate.integrations.group",
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
    expect(route?.component).toBeTypeOf("function");
  });

  test("mounts the loopback `/callback` alias for fixed public clients", () => {
    const loopback = (integrate.routes ?? []).find(
      (item) => item.name === "integrate.connect.callbackLoopback",
    );
    // Pinned to `/callback` to match the seeded `loopback_redirect_path` (see
    // tests/test_connections.py) — drift on either side fails a test, not a connect.
    expect(loopback?.path).toBe("/callback");
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
    expect(integrate.forms?.["integrate.Credential"]).toBeDefined();
  });

  test("references the landing route from exactly one menu item (chrome derivation)", () => {
    const tree = MenuTree.from(integrate.menus as readonly ChromeMenuItem[]);
    expect(tree.itemsForRoute("integrate.integrations")).toHaveLength(1);
  });

  test("registers its glyphs", () => {
    for (const name of [
      "connect",
      "integration",
      "vendor",
      "webhook",
    ] as const) {
      expect(integrate.icons?.[name]).toBeDefined();
    }
  });

  test("contributes its lifecycle verbs against the MTI parent every subtype inherits", () => {
    // Declared at `integrate.Integration#…` rather than at the kind, so each
    // subtype's form inherits them through its canonical label, and a bridge
    // specializes one with a `variant` without this addon naming the subtype.
    const subtype = [INTEGRATION_MODEL, "messaging.Channel"];
    expect(childIds("form#actions", subtype)).toEqual([
      "integrate.lifecycle.pause",
      "integrate.lifecycle.resume",
    ]);
    expect(childIds("form#actions-menu", subtype)).toEqual([
      "integrate.lifecycle.disconnect",
      "integrate.connection.test",
    ]);
    // An unrelated model's form gets none of them.
    expect(childIds("form#actions", ["notes.Note"])).toEqual([]);
  });

  test("ships no Connect verb — a handshake belongs to the addon that owns the vendor", () => {
    // `mark_integration_connected` is a credential-free flag flip, correct only
    // as the inverse of a pause. Contributing it as Connect shadowed the real
    // OAuth/CardDAV/WhatsApp handshakes, so it backs Resume and nothing else.
    const ids = ["form#actions", "form#actions-menu"].flatMap((address) => childIds(address, [INTEGRATION_MODEL]));
    expect(ids).not.toContain("integrate.lifecycle.connect");
    expect(integrate.i18n?.integrate?.["lifecycle.connect"]).toBeUndefined();
  });

  test("contributes one Streams section to the Integration parent without a new route", () => {
    const sections = resolveContainer(composed, "form#sections", { models: [INTEGRATION_MODEL, "messaging.Channel"] });

    expect(sections.map(({ id, address, impl }) => ({ id, address, impl }))).toEqual([
      { id: INTEGRATION_STREAMS_TAB_ID, address: `${INTEGRATION_MODEL}#sections`, impl: undefined },
    ]);
    expect((integrate.routes ?? []).some((route) => /stream|discrepancy|record-link/.test(route.name)))
      .toBe(false);
    expect(integrate.widgets?.["angee.integrate.integrationSyncCursor"]).toBeDefined();
  });
});
