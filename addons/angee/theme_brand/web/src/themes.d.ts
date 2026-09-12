import type { ThemeDefinition } from "@angee/ui/theme";
export interface BrandThemeOptions { brand: string; accent: string; radius: string }
export const themes: readonly [ThemeDefinition<BrandThemeOptions>];
