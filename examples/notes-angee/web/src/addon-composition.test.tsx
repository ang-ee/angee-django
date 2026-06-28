// @vitest-environment happy-dom

import { composeAddons, defineBaseAddon } from "@angee/app";
import { baseIcons, getIcon, type ComposedMenuItem } from "@angee/ui";
import notes from "@angee-example/notes-web";
import agents from "@angee/agents";
import iam from "@angee/iam";
import integrate from "@angee/integrate";
import knowledge from "@angee/knowledge";
import messaging from "@angee/messaging";
import operator from "@angee/operator";
import parties from "@angee/parties";
import platform from "@angee/platform";
import resources from "@angee/resources-addon";
import storage from "@angee/storage";
import { describe, expect, test } from "vitest";

const authAddon = defineBaseAddon({
  id: "auth",
  routes: [
    { name: "auth.login", path: "/login", layout: "public", component: () => null },
  ],
});

// The full addon set the host composes (mirrors main.tsx). `composeAddons` is
// fail-fast on any id collision — icon, route, menu, i18n key, widget, form,
// preview — but that check runs only at app boot, not during typecheck/build, so
// a clash between a base glyph and an addon's contribution would otherwise ship
// green and crash `angee dev`. This guard composes every addon so the gate
// catches it.
const ADDONS = [
  notes,
  authAddon,
  iam,
  parties,
  messaging,
  integrate,
  agents,
  operator,
  storage,
  knowledge,
  resources,
  platform,
] as const;
const HOST_ADDONS = [{ id: "base", icons: baseIcons }, ...ADDONS] as const;

describe("full addon composition", () => {
  test("composes every addon without an id collision", () => {
    expect(() => composeAddons(HOST_ADDONS)).not.toThrow();
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
