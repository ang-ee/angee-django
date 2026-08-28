import type { WidgetField } from "./types";

export function widgetLabel(
  field: WidgetField | undefined,
  fallback: string,
): string {
  return typeof field?.label === "string" ? field.label : fallback;
}
