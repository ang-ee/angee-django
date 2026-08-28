import { titleCase } from "./titleCase";

/**
 * Humanize a bare enum/state member name for display (`IN_REVIEW` -> `In Review`).
 */
export function statusLabel(value: string): string {
  return titleCase(value.toLowerCase());
}
