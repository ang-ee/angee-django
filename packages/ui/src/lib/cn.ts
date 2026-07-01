import { extendTailwindMerge } from "tailwind-merge";

import { ANGEE_TW_MERGE_CONFIG } from "./tailwind-merge-config";

/**
 * `cn` — class concatenation with conflict resolution.
 *
 * The same Angee Tailwind Merge config is used by `tailwind-variants`.
 * Keep class-group ownership in `tailwind-merge-config.ts`, not split
 * between ad hoc merge helpers and component recipes.
 */
const twMergeCustom = extendTailwindMerge(ANGEE_TW_MERGE_CONFIG);

type ClassValue = string | number | null | undefined | false | ClassValue[];

export function cn(...inputs: ClassValue[]): string {
  return twMergeCustom(flatten(inputs).join(" "));
}

function flatten(values: ClassValue[]): string[] {
  const out: string[] = [];
  for (const value of values) {
    if (!value) continue;
    if (Array.isArray(value)) out.push(...flatten(value));
    else if (typeof value === "string") out.push(value);
    else if (typeof value === "number") out.push(String(value));
  }
  return out;
}
