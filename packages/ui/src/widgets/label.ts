import type { WidgetField } from "./types";

/**
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function widgetLabel(
  field: WidgetField | undefined,
  fallback: string,
): string {
  return typeof field?.label === "string" ? field.label : fallback;
}
