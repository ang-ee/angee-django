import { defineBaseAddon } from "@angee/app";
import { createElement } from "react";
import { WebsiteAppearanceTool } from "./WebsiteAppearanceTool";
import { enAppearanceIntegrateMessages } from "./i18n";

export default defineBaseAddon({
  id: "appearance.integrate",
  i18n: { appearanceIntegrate: enAppearanceIntegrateMessages },
  containers: {
    "appearance.settings#tools": { "appearance.integrate.website": { content: createElement(WebsiteAppearanceTool) } },
  },
});
