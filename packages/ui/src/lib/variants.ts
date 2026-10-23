import { createTV, type StringToBoolean } from "tailwind-variants";

import { ANGEE_TW_MERGE_CONFIG } from "./tailwind-merge-config";

export type { VariantProps } from "tailwind-variants";

/**
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export const tv = createTV({
  twMergeConfig: ANGEE_TW_MERGE_CONFIG,
});

type VariantMap = Record<string, Record<string, unknown>> | undefined;
type AxisValue<Key> = Key extends number ? `${Key}` : StringToBoolean<Key>;

/**
 * Sorted specimen axes from the native recipe, including inherited variants.
 * @kind null
 * @reason Runtime styling utility; reads the native recipe's variant contract.
 */
export function recipeAxes<const Variants extends VariantMap>(recipe: { variants: Variants }) {
  const variants: NonNullable<VariantMap> = recipe.variants ?? {};
  return Object.fromEntries(Object.entries(variants).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    .map(([key, values]) => [key, Object.keys(values).sort().map(value =>
      value === "true" ? true : value === "false" ? false : value,
    )])) as unknown as { readonly [Key in keyof NonNullable<Variants>]: readonly AxisValue<keyof NonNullable<Variants>[Key]>[] };
}
