import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution } from "@angee/ui/theme";

import { themes } from "./themes.mjs";

export default defineBaseAddon({
  id: "theme.fyltr",
  themes: [defineThemeContribution({ definition: themes[0] })],
  i18n: {
    themes: {
      "fyltr.label": "Fyltr",
      "fyltr.description": "The fyltr.ai green identity on sovereign charcoal surfaces.",
    },
  },
});
