import type { I18nResources } from "@angee/sdk";

export interface OperatorI18nSection {
  id: string;
  label: string;
}

export function enOperatorBundleForSections(
  sections: readonly OperatorI18nSection[],
): I18nResources {
  const titles: Record<string, string> = {};
  for (const section of sections) {
    titles[`section.${section.id}.title`] = section.label;
  }
  return { operator: titles } satisfies I18nResources;
}
