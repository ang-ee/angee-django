import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution } from "@angee/ui/theme";
import { themes } from "./themes.mjs";
export default defineBaseAddon({ id: "theme.aurora", themes: [defineThemeContribution({ definition: themes[0] })], i18n: { themes: { "aurora.label": "Aurora", "aurora.description": "Luminous green surfaces, violet accents, and generous curves." } } });
