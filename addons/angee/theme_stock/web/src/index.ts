import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, ThemeCustomizationEditor } from "@angee/ui/theme";
import { themes } from "./themes.mjs";
export default defineBaseAddon({
  id: "theme.stock",
  themes: [defineThemeContribution({ definition: themes[0], optionsEditor: ThemeCustomizationEditor })],
  i18n: {
    themes: {
      "stock.label": "Default",
      "stock.description": "The original blue Angee design, ready to personalize.",
    },
  },
});
