import { defineBaseAddon } from "@angee/app"; import { defineThemeContribution } from "@angee/ui/theme"; import { themes } from "./themes.mjs";
export default defineBaseAddon({ id: "theme.warm-red", themes: [defineThemeContribution({ definition: themes[0] })], i18n: { themes: { "warmRed.label": "Warm Red", "warmRed.description": "Warm neutral surfaces with a strong red accent." } } });
