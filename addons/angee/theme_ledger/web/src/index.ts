import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, ThemeCustomizationEditor } from "@angee/ui/theme";

import { themes } from "./themes.mjs";

export default defineBaseAddon({
  id: "theme.ledger",
  themes: [defineThemeContribution({ definition: themes[0], optionsEditor: ThemeCustomizationEditor })],
  i18n: {
    themes: {
      "ledger.label": "Ledger",
      "ledger.description": "Ink on warm paper with a solar accent: one weight, flat borders, monochrome charts.",
    },
  },
});
