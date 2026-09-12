import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution, ThemeCustomizationEditor } from "@angee/ui/theme";
import { themes } from "./themes.mjs";

export default defineBaseAddon({ id: "theme.brand", themes: [defineThemeContribution({ definition: themes[0], optionsEditor: ThemeCustomizationEditor })], i18n: { themes: { "brand.label": "Brand", "brand.description": "Build a complete design from bounded identity, surface, status, logo, type, shape, density, and elevation choices." } } });
