import type { DrawerContribution, SlotContribution } from "@angee/ui/runtime";
import {
  isSurfaceSlotAdmitted,
  type SurfaceAdmission,
  type SurfacePresentation,
} from "@angee/ui/chrome/surface-policy";
import type { MenuTree } from "@angee/ui/chrome/menu-tree";

import type { AddonRoute } from "./define-addon";

export type { SurfaceAdmission } from "@angee/ui/chrome/surface-policy";

export interface SurfaceDeclaration {
  admit?: SurfaceAdmission;
  /** Hidden, or the ordered aside tabs selected for this scope. */
  chatter?: SurfacePresentation["chatter"];
  shell?: SurfacePresentation["shell"];
}

/** A menu-root scope; `route` narrows one route and its descendants in that app. */
export interface AppSurface extends SurfaceDeclaration {
  app: string;
  route?: string;
}

export type ResolvedRoutePolicy = SurfacePresentation;

/** Compile app and route scopes once, using the same specificity as vocabulary. */
export function routePolicyIndex(
  routes: readonly AddonRoute[],
  declarations: readonly AppSurface[],
  menus: MenuTree,
  inventory: {
    slots: readonly SlotContribution[];
    drawers: readonly DrawerContribution[];
  },
): (app?: string, routeName?: string) => ResolvedRoutePolicy {
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const scopes = new Map<string, AppSurface>();
  const slotIds = new Map<string, Set<string>>();
  for (const entry of inventory.slots) {
    const ids = slotIds.get(entry.slot) ?? new Set<string>();
    ids.add(entry.id);
    slotIds.set(entry.slot, ids);
  }
  const drawerIds = new Set(inventory.drawers.map((entry) => entry.id));
  for (const declaration of declarations) {
    if (!menus.roots.some((root) => root.id === declaration.app)) {
      throw new Error(`Surface references unknown app "${declaration.app}".`);
    }
    if (declaration.route && !routesByName.has(declaration.route)) {
      throw new Error(`Surface references unknown route "${declaration.route}".`);
    }
    const key = `${declaration.app}\0${declaration.route ?? ""}`;
    if (scopes.has(key)) throw new Error(`Surface scope "${declaration.app}:${declaration.route ?? "*"}" is declared twice.`);
    for (const [slot, ids] of Object.entries(declaration.admit?.slots ?? {})) {
      for (const id of ids) {
        if (!slotIds.get(slot)?.has(id)) throw new Error(`Surface scope "${declaration.app}:${declaration.route ?? "*"}" admits unknown slot contribution "${slot}:${id}".`);
      }
    }
    for (const id of declaration.admit?.drawers ?? []) {
      if (!drawerIds.has(id)) throw new Error(`Surface scope "${declaration.app}:${declaration.route ?? "*"}" admits unknown drawer contribution "${id}".`);
    }
    // An aside id may be published by its page after the route mounts. Chatter
    // validates it against built-in, contributed and published tabs at runtime.
    scopes.set(key, declaration);
  }

  const cache = new Map<string, ResolvedRoutePolicy>();
  return (app, routeName) => {
    const key = `${app ?? ""}\0${routeName ?? ""}`;
    const cached = cache.get(key);
    if (cached) return cached;
    const chain: SurfaceDeclaration[] = [];
    const visited = new Set<string>();
    let route = routeName ? routesByName.get(routeName) : undefined;
    while (route && !visited.has(route.name)) {
      visited.add(route.name);
      const scope = scopes.get(`${app ?? ""}\0${route.name}`);
      if (scope) chain.unshift(scope);
      route = route.parent ? routesByName.get(route.parent) : undefined;
    }
    const appScope = scopes.get(`${app ?? ""}\0`);
    if (appScope) chain.unshift(appScope);
    let admit: SurfaceAdmission | undefined;
    let chatter: SurfacePresentation["chatter"];
    let shell: SurfacePresentation["shell"];
    for (const scope of chain) {
      if (scope.admit) admit = narrowAdmission(admit, scope.admit);
      if (scope.chatter !== undefined) chatter = scope.chatter;
      if (scope.shell) shell = { ...shell, ...scope.shell };
    }
    const resolved: ResolvedRoutePolicy = {
      ...(admit ? { admit } : {}),
      ...(chatter !== undefined ? { chatter } : {}),
      ...(shell ? { shell } : {}),
    };
    cache.set(key, resolved);
    return resolved;
  };
}

function narrowAdmission(current: SurfaceAdmission | undefined, next: SurfaceAdmission): SurfaceAdmission {
  if (!current) return next;
  const slots: Record<string, readonly string[]> = { ...current.slots };
  for (const [slot, ids] of Object.entries(next.slots ?? {})) {
    const inherited = current.slots?.[slot];
    slots[slot] = inherited === undefined ? ids : ids.filter((id) => inherited.includes(id));
  }
  const narrow = (inherited: readonly string[] | undefined, requested: readonly string[] | undefined) =>
    requested === undefined ? inherited : inherited === undefined ? requested : requested.filter((id) => inherited.includes(id));
  const aside = narrow(current.aside, next.aside);
  const drawers = narrow(current.drawers, next.drawers);
  return {
    ...(Object.keys(slots).length ? { slots } : {}),
    ...(aside !== undefined ? { aside } : {}),
    ...(drawers !== undefined ? { drawers } : {}),
  };
}

/** Project only named addresses; omitted extension points remain unchanged. */
export function admittedContributions<T extends { id: string; slot?: string }>(
  entries: readonly T[],
  admit: SurfaceAdmission | undefined,
  kind: "slots" | "drawers" | "aside",
): readonly T[] {
  if (!admit) return entries;
  return entries.filter((entry) => {
    if (kind === "slots") return isSurfaceSlotAdmitted(admit, entry.slot ?? "", entry.id);
    const ids = admit[kind];
    return ids === undefined || ids.includes(entry.id);
  });
}
