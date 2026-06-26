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
});

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
