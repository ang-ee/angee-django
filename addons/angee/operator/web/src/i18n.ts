import type { BaseMenuItem } from "@angee/base";
import type { I18nResources } from "@angee/sdk";

export function enOperatorBundleForMenu(
  menu: BaseMenuItem,
): I18nResources {
  const titles: Record<string, string> = {};
  for (const section of menu.children ?? []) {
    const sectionId = section.id ?? section.route;
    if (!sectionId || !section.label) continue;
    titles[`section.${sectionId}.title`] = section.label;
  }
  return { operator: titles } satisfies I18nResources;
}
