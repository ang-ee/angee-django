import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution } from "@angee/ui/theme";
import { themes } from "./themes.mjs";
export default defineBaseAddon({ id: "theme.carbon", themes: [defineThemeContribution({ definition: themes[0] })], i18n: { themes: { "carbon.label": "Carbon", "carbon.description": "Crisp enterprise surfaces with compact square controls." } } });
