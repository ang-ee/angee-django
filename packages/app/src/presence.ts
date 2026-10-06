import type { ChromeMenuNode, MenuTree } from "@angee/ui/chrome/menu-tree";
import { isPresent, type ComposedContainers, type RuntimeComposition } from "@angee/ui/runtime";

/** What a `requires` names: a model's permission, `<app_label.ModelName>#<permission>`. */
const REQUIRES_REF = /^\w+\.\w+#\w+$/;

/** Every `requires` a composition declares: menu ids, and container children keyed `address/id`, to their refs. */
export type PresenceRequirements = NonNullable<RuntimeComposition["requires"]>;

/**
 * Collect the composition's `requires` from its menu nodes and container
 * children. A ref not shaped `<app_label.ModelName>#<permission>` fails at
 * boot; the server refuses a well-shaped one naming an unknown model or permission.
 */
export function presenceRequirements(menuTree: MenuTree, containers: ComposedContainers): PresenceRequirements {
  const checked = (ref: string, where: string): string => {
    if (!REQUIRES_REF.test(ref)) throw new Error(`${where} requires "${ref}", which is not "<app_label.ModelName>#<permission>".`);
    return ref;
  };
  const menus: Record<string, string> = {};
  for (const item of menuTree.byId.values()) {
    if (item.requires !== undefined) menus[item.id] = checked(item.requires, `Menu item "${item.id}"`);
  }
  const children: Record<string, string> = {};
  for (const [address, declared] of Object.entries(containers.children)) {
    for (const child of declared) {
      if (child.requires !== undefined) children[`${address}/${child.id}`] = checked(child.requires, `Child "${child.id}" of "${address}"`);
    }
  }
  return { menus, containers: children };
}

/** The distinct refs the identity read asks `current_user.permitted` about, sorted. */
export function presenceRefs(requirements: PresenceRequirements): readonly string[] {
  return [...new Set([...Object.values(requirements.menus), ...Object.values(requirements.containers)])].sort();
}

/**
 * The menu nodes a session holding `permitted` lacks: each node whose
 * `requires` it does not hold, with its whole logical subtree, so an included
 * app's items leave with it even where the navigation lifts them.
 */
export function absentMenuNodes(menuTree: MenuTree, permitted: readonly string[] | undefined): ReadonlySet<string> {
  const absent = new Set<string>();
  const leave = (item: ChromeMenuNode): void => {
    absent.add(item.id);
    for (const child of item.children ?? []) leave(child);
  };
  for (const item of menuTree.byId.values()) {
    if (!absent.has(item.id) && !isPresent(item.requires, permitted)) leave(item);
  }
  return absent;
}
