import type { ThemeDefinition } from "@angee/ui/theme";

export interface PaperThemeOptions {
  paperTint: "cream" | "white";
}

export const themes: readonly [ThemeDefinition<PaperThemeOptions>];
