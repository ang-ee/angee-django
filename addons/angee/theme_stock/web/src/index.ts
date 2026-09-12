import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution } from "@angee/ui/theme";
import { themes } from "./themes.mjs";
export default defineBaseAddon({
  id: "theme.stock",
  themes: [defineThemeContribution({ definition: themes[0] })],
  i18n: { themes: { "stock.label": "Stock", "stock.description": "The familiar Angee interface." } },
});
