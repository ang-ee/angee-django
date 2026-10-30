import { defineBaseAddon } from "@angee/app"; import { defineThemeContribution } from "@angee/ui/theme"; import { themes } from "./themes.mjs";
export default defineBaseAddon({ id: "theme.midnight", themes: [defineThemeContribution({ definition: themes[0] })], i18n: { themes: { "midnight.label": "Midnight", "midnight.description": "Deep navy surfaces with clear blue highlights." } } });
