import { titleCase } from "./titleCase";
import type { ModelEnumValueMetadata } from "@angee/metadata";

/**
 * Humanize a bare enum/state member name for display (`IN_REVIEW` -> `In Review`).
 */
export function statusLabel(value: string): string {
  return titleCase(value.toLowerCase());
}

export function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

/** Use the resource's authored label before humanizing an enum member. */
export function enumValueLabel(value: ModelEnumValueMetadata): string {
  return value.description ?? statusLabel(value.value);
}
