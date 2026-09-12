import { defineBaseAddon } from "@angee/app";
import { USER_MENU_ITEMS_SLOT, DropdownMenu, Glyph } from "@angee/ui";
import { lazyRouteComponent, useNavigate } from "@tanstack/react-router";
import { createElement } from "react";
import { Palette } from "lucide-react";
import { enAppearanceMessages } from "./i18n";

export const APPEARANCE_TOOLS_SLOT = "appearance.settings.tools";

function AppearanceMenuItem() {
  const navigate = useNavigate();
  return <DropdownMenu.Item onClick={() => void navigate({ to: "/settings/appearance" })}>
    <Glyph name="appearance" /><span className="flex-1 truncate">Appearance</span>
  </DropdownMenu.Item>;
}

export default defineBaseAddon({
  id: "appearance",
  routes: [{ name: "appearance.settings", path: "/settings/appearance", component: lazyRouteComponent(() => import("./views/AppearanceSettingsPage"), "AppearanceSettingsPage") }],
  menus: [{ id: "appearance", label: "Appearance", icon: "appearance", group: "platform", route: "appearance.settings" }],
  i18n: { appearance: enAppearanceMessages },
  icons: { appearance: Palette },
  slots: [{ slot: USER_MENU_ITEMS_SLOT, id: "appearance.settings", content: createElement(AppearanceMenuItem) }],
});
