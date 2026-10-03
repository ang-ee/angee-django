import { isMenuDeclarationList, type BaseAddon } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";


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
  const toolbar = (addon.containers as Record<string, Record<string, unknown> | undefined> | undefined)?.["messaging.channels#toolbar"];
  if (!toolbar || !(`${addon.id}.connect` in toolbar)) {
    throw new Error(
      `Channel bridge "${addon.id}" must contribute its connect action to the channels toolbar.`,
    );
  }
}
