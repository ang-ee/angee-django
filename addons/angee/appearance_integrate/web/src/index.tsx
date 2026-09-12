import { defineBaseAddon } from "@angee/app";
import { APPEARANCE_TOOLS_SLOT } from "@angee/appearance";
import { createElement } from "react";
import { WebsiteAppearanceTool } from "./WebsiteAppearanceTool";
export default defineBaseAddon({ id: "appearance.integrate", slots: [{ slot: APPEARANCE_TOOLS_SLOT, id: "appearance.website", content: createElement(WebsiteAppearanceTool) }] });
