import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution } from "@angee/ui/theme";

import { themes } from "./themes.mjs";

export default defineBaseAddon({
  id: "theme.angee",
  themes: [defineThemeContribution({ definition: themes[0] })],
  i18n: {
    themes: {
      "angee.label": "Angee",
      "angee.description": "The angee.ai gold identity on crisp neutral surfaces.",
    },
  },
});
