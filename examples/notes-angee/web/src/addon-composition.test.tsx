// @vitest-environment happy-dom

import { composeAddons, defineBaseAddon } from "@angee/app";
import {
  baseIcons,
  formViewRecordActionsSlot,
  getIcon,
  type ComposedMenuItem,
} from "@angee/ui";
import agents from "@angee/agents";
import { describe, expect, test } from "vitest";

import { composedAddons } from "../../runtime/web/app";

const authAddon = defineBaseAddon({
  id: "auth",
  routes: [
    { name: "auth.login", path: "/login", layout: "public", component: () => null },
  ],
});

// The full addon set the host composes, read from the generated runtime `main.tsx`
// composes — not a hand-listed copy, which silently drifted from it and left the
// two messaging integration addons uncomposed here (the reason a slot-id
// collision between integrate and messaging-integrate-whatsapp was invisible to
// CI). `composeAddons` is fail-fast on any id collision — icon, route, menu,
// i18n key, widget, form, preview, slot entry — but that check runs only at app
// boot, not during typecheck/build, so a clash would otherwise ship green and
// crash `angee dev`. This guard composes exactly what boots.
const HOST_ADDONS = [
  { id: "base", icons: baseIcons },
  ...composedAddons,
  authAddon,
] as const;

describe("full addon composition", () => {
  test("composes every addon without an id collision", () => {
    expect(() => composeAddons(HOST_ADDONS)).not.toThrow();
  });

  test("composes integrate's and WhatsApp's record verbs into per-key slots", () => {
    // Both addons contribute the Resume/Disconnect ids. They compose only because
    // each is scoped to a key it owns: integrate to the MTI parent it owns, and
    // WhatsApp to its own backend's impl key — never to `messaging.Channel`, which
    // `messaging` owns and where WhatsApp's entries would displace integrate's for
    // every channel, IMAP's included. Contributed to one shared key, the shared ids
    // would collide and `composeAddons` would throw. The split is also what lets
    // FormView pick a row's entry by declared specificity, not addon array order.
    const composed = composeAddons(HOST_ADDONS);
    const entryIds = (slot: string): string[] =>
      composed.slots.filter((entry) => entry.slot === slot).map((entry) => entry.id);

    expect(entryIds(formViewRecordActionsSlot("integrate.Integration"))).toEqual([
      "integrate.lifecycle.pause",
      "integrate.lifecycle.resume",
      "integrate.lifecycle.disconnect",
    ]);
    expect(
      entryIds(formViewRecordActionsSlot("messaging.Channel", "whatsapp")),
    ).toEqual([
      // The two ids WhatsApp owns outright — a QR pairing and a read of its
      // result have no shared verb to specialize — then the two it replaces.
      "messaging-integrate-whatsapp.connect",
      "messaging-integrate-whatsapp.pairing",
      "integrate.lifecycle.resume",
      "integrate.lifecycle.disconnect",
    ]);
    // No addon claims the bare channel-model key, so an IMAP channel resolves only
    // integrate's canonical verbs.
    expect(entryIds(formViewRecordActionsSlot("messaging.Channel"))).toEqual([]);
  });

  test("resolves every menu icon through the composed glyph registry", () => {
    const composed = composeAddons(HOST_ADDONS);
    expect(unresolvedMenuIcons(composed.menus, composed.icons)).toEqual([]);
  });

  test("composes the operator logs drawer with a resolvable glyph", () => {
    const composed = composeAddons(HOST_ADDONS);
    const logsDrawer = composed.drawers.find(
      (drawer) => drawer.edge === "bottom" && drawer.id === "logs",
    );
    expect(logsDrawer?.title).toBeTruthy();
    expect(logsDrawer?.icon).toBe("operator-logs");
    expect(getIcon(composed.icons, logsDrawer?.icon ?? "")).toBeTruthy();
  });

  test("registers the full-page sessions route + child placeholder + Sessions nav item", () => {
    const routes = agents.routes ?? [];
    const sessions = routes.find((route) => route.name === "agents.sessions");
    expect(sessions?.path).toBe("/agents/sessions");
    expect(sessions?.component).toBeTruthy();

    // The `$id` child is the URL placeholder only — no component, parented to the page
    // route, so the parent stays mounted across `:id` changes (the keep-alive substrate).
    const child = routes.find((route) => route.name === "agents.session");
    expect(child?.path).toBe("/agents/sessions/$id");
    expect(child?.parent).toBe("agents.sessions");
    expect(child?.component).toBeUndefined();

    const item = findMenuItem(agents.menus ?? [], "agents.sessions");
    expect(item?.route).toBe("agents.sessions");
  });
});

type NavNode = { id?: string; route?: string; children?: readonly NavNode[] };

function findMenuItem(items: readonly NavNode[], id: string): NavNode | undefined {
  for (const item of items) {
    if (item.id === id) return item;
    const found = item.children ? findMenuItem(item.children, id) : undefined;
    if (found) return found;
  }
  return undefined;
}

function unresolvedMenuIcons(
  items: readonly ComposedMenuItem[],
  icons: Readonly<Record<string, unknown>>,
): { id: string; icon: string }[] {
  const unresolved: { id: string; icon: string }[] = [];
  for (const item of items) {
    const icon = item.icon ?? item.id;
    if (!getIcon(icons, icon)) unresolved.push({ id: item.id, icon });
    if (item.children) unresolved.push(...unresolvedMenuIcons(item.children, icons));
  }
  return unresolved;
}
