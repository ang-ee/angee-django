import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, ThemeCustomizationEditor } from "@angee/ui/theme";

import { themes } from "./themes.mjs";

export default defineBaseAddon({
  id: "theme.angee",
  themes: [defineThemeContribution({ definition: themes[0], optionsEditor: ThemeCustomizationEditor })],
  i18n: {
    themes: {
      "angee.label": "Angee",
      "angee.description": "The angee.ai gold identity on crisp neutral surfaces, ready to personalize.",
    },
  },
});
