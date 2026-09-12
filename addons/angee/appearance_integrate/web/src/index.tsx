import { defineBaseAddon } from "@angee/app";
import { APPEARANCE_TOOLS_SLOT } from "@angee/appearance";
import { createElement } from "react";
import { WebsiteAppearanceTool } from "./WebsiteAppearanceTool";
import { enAppearanceIntegrateMessages } from "./i18n";

export default defineBaseAddon({
  id: "appearance.integrate",
  i18n: { appearanceIntegrate: enAppearanceIntegrateMessages },
  slots: [{ slot: APPEARANCE_TOOLS_SLOT, id: "appearance.website", content: createElement(WebsiteAppearanceTool) }],
});
