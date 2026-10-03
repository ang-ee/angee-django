import { isMenuDeclarationList, type BaseAddon } from "@angee/app";
import { containerChildren, expectValidBaseAddon } from "@angee/app/testing";

import { CHANNEL_MODEL } from "./documents";


/** Assert the navigation/connect contract shared by every channel bridge addon. */
export function expectValidChannelBridgeAddon(addon: BaseAddon): void {
  expectValidBaseAddon(addon);
  const menus = isMenuDeclarationList(addon.menus) ? addon.menus : [];
  if (menus.length !== 1) {
    throw new Error(`Channel bridge "${addon.id}" must contribute one menu item.`);
  }
  const menu = menus[0]!;
  if (
    menu.route !== "messaging.channels"
    || menu.parentId !== "messaging"
    || menu.icon !== "channel"
  ) {
    throw new Error(
      `Channel bridge "${addon.id}" must target the shared messaging channels menu.`,
    );
  }
  if (!containerChildren(addon, "messaging.channels#toolbar").some(([id]) => id === `${addon.id}.connect`)) {
    throw new Error(
      `Channel bridge "${addon.id}" must contribute its connect action to the channels toolbar.`,
    );
  }
}

/** Assert every channel verb a bridge adds shows only on its own rows: an `impl` child or a variant of integrate's verb. */
export function expectChannelVerbsScoped(addon: BaseAddon, key: string): void {
  const verbs = ["#actions", "#actions-menu"].flatMap((name) => containerChildren(addon, `${CHANNEL_MODEL}${name}`));
  if (verbs.length === 0) throw new Error(`Channel bridge "${addon.id}" contributes no channel verbs.`);
  for (const [id, verb] of verbs) {
    if ((verb.impl ?? verb.variant?.impl) !== key) {
      throw new Error(`Channel bridge "${addon.id}" verb "${id}" must be scoped to "${key}" rows (impl or variant).`);
    }
  }
}
