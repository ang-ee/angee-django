import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { MenuTree, resolveContainer } from "@angee/ui";
import { describe, expect, test } from "vitest";

import integrate from "./index";
import provenance from "@angee/provenance";
import timeline from "@angee/timeline";
import { INTEGRATION_MODEL, INTEGRATION_RESUME_ACTION_ID } from "./IntegrationLifecycleActions";
import { INTEGRATION_STREAMS_TAB_ID } from "./IntegrationStreams";

const composed = composeAddons([timeline, provenance, integrate], { canonicalModelLabel: (model) => model }).containers;
/** The child ids one form container resolves for a subtype record. */
const childIds = (address: string, models: readonly string[]) =>
  resolveContainer(composed, address, { models }).map((child) => child.id);

const declaredMenus = composeAddons([{ id: integrate.id, menus: integrate.menus, routes: integrate.routes }], { canonicalModelLabel: (model) => model }).menuComposition.logical;

describe("integrate addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(integrate)).not.toThrow();
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

  test("registers the Credential create form override", () => {
    expect(integrate.forms?.["integrate.Credential"]).toBeDefined();
  });

  test("references the landing route from exactly one menu item (chrome derivation)", () => {
    const tree = MenuTree.from(declaredMenus);
    expect(tree.itemsForRoute("integrate.integrations")).toHaveLength(1);
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
      "integrate.connection.retryDiscovery",
    ]);
    // An unrelated model's form gets none of them.
    expect(childIds("form#actions", ["notes.Note"])).toEqual([]);
  });

  test("projects model admission for inherited Resume without private form fields", () => {
    const actions = resolveContainer(composed, "form#actions", { models: [INTEGRATION_MODEL, "messaging.Channel"] });
    expect(actions.find(({ id }) => id === INTEGRATION_RESUME_ACTION_ID)?.requiredFields)
      .toEqual(["can_resume"]);
  });

  test("ships no Connect verb — a handshake belongs to the addon that owns the vendor", () => {
    // The model-owned Resume repeats discovery for an existing connection.
    // A vendor still owns the UX that selects a new account.
    const ids = ["form#actions", "form#actions-menu"].flatMap((address) => childIds(address, [INTEGRATION_MODEL]));
    expect(ids).not.toContain("integrate.lifecycle.connect");
    expect(integrate.i18n?.integrate?.["lifecycle.connect"]).toBeUndefined();
  });

  test("contributes one Streams section to the Integration parent without a new route", () => {
    const sections = resolveContainer(composed, "form#sections", { models: [INTEGRATION_MODEL, "messaging.Channel"] });

    expect(sections.map(({ id, address, impl }) => ({ id, address, impl }))).toEqual([
      { id: INTEGRATION_STREAMS_TAB_ID, address: `${INTEGRATION_MODEL}#sections`, impl: undefined },
    ]);
    expect((integrate.routes ?? []).some((route) => /stream|failure|record-link/.test(route.name)))
      .toBe(false);
    expect(integrate.widgets?.["angee.integrate.integrationSyncCursor"]).toBeDefined();
  });
});
