import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, ThemeCustomizationEditor } from "@angee/ui/theme";

import { themes } from "./themes.mjs";

export default defineBaseAddon({
  id: "theme.fyltr",
  themes: [defineThemeContribution({ definition: themes[0], optionsEditor: ThemeCustomizationEditor })],
  i18n: {
    themes: {
      "fyltr.label": "Fyltr",
      "fyltr.description": "The fyltr.ai green identity on sovereign charcoal surfaces, ready to personalize.",
    },
  },
});
