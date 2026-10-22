import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, ThemeCustomizationEditor } from "@angee/ui/theme";
import { themes } from "./themes.mjs";
export default defineBaseAddon({
  id: "theme.stock",
  themes: [defineThemeContribution({ definition: themes[0], optionsEditor: ThemeCustomizationEditor })],
  i18n: {
    themes: {
      "stock.label": "Default",
      "stock.description": "The Angee default: an indigo brand on neutral surfaces, ready to personalize.",
    },
  },
});
