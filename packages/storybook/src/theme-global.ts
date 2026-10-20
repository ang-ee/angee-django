import { defineThemeContribution } from "@angee/ui";
import { themes as angeeThemes } from "@angee/theme-angee/themes";
import { themes as auroraThemes } from "@angee/theme-aurora/themes";
import { themes as carbonThemes } from "@angee/theme-carbon/themes";
import { themes as fyltrThemes } from "@angee/theme-fyltr/themes";
import { themes as ledgerThemes } from "@angee/theme-ledger/themes";
import { themes as midnightThemes } from "@angee/theme-midnight/themes";
import { themes as stockThemes } from "@angee/theme-stock/themes";
import { themes as warmRedThemes } from "@angee/theme-warm-red/themes";
import { storybookValueLookup } from "./url-values";

export const previewThemes = [
  defineThemeContribution({ definition: stockThemes[0] }),
  defineThemeContribution({ definition: angeeThemes[0] }),
  defineThemeContribution({ definition: fyltrThemes[0] }),
  defineThemeContribution({ definition: ledgerThemes[0] }),
  defineThemeContribution({ definition: carbonThemes[0] }),
  defineThemeContribution({ definition: auroraThemes[0] }),
  defineThemeContribution({ definition: midnightThemes[0] }),
  defineThemeContribution({ definition: warmRedThemes[0] }),
];

const idsByGlobal = storybookValueLookup(previewThemes.map(({ definition }) => definition.id));
const globalsById = new Map([...idsByGlobal].map(([value, id]) => [id, value]));

export function themeGlobalValue(themeId: string): string {
  const value = globalsById.get(themeId);
  if (!value) throw new Error(`Unregistered Storybook theme: ${themeId}`);
  return value;
}

export function previewThemeId(value: unknown): string {
  return typeof value === "string"
    ? idsByGlobal.get(value) ?? (globalsById.has(value) ? value : "angee.stock")
    : "angee.stock";
}
