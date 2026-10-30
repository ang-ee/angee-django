import { defineBaseAddon } from "@angee/app";
import { DropdownMenu, Glyph, useRouteHref } from "@angee/ui";
import { lazyRouteComponent, useNavigate } from "@tanstack/react-router";
import { createElement } from "react";
import { Palette } from "lucide-react";
import { enAppearanceMessages, useAppearanceT } from "./i18n";


function AppearanceMenuItem() {
  const t = useAppearanceT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  return <DropdownMenu.Item onClick={() => void navigate({ to: routeHref("appearance.settings") })}>
    <Glyph name="appearance" /><span className="flex-1 truncate">{t("menu.appearance")}</span>
  </DropdownMenu.Item>;
}

export default defineBaseAddon({
  id: "appearance",
  routes: [{ name: "appearance.settings", path: "/settings/appearance", component: lazyRouteComponent(() => import("./views/AppearanceSettingsPage"), "AppearanceSettingsPage") }],
  // Personal: the user menu links here, so every selected app keeps it in Settings.
  menus: {
    "appearance": { label: "Appearance", icon: "appearance", group: "platform", personal: true, route: "appearance.settings" },
  },
  i18n: { appearance: enAppearanceMessages },
  icons: { appearance: Palette },
  containers: {
    "shell#user-menu": { "appearance.settings": { sequence: 10, content: createElement(AppearanceMenuItem) } },
    "appearance.settings#tools": {},
  },
});
