import { createTV } from "tailwind-variants";

import { ANGEE_TW_MERGE_CONFIG } from "./tailwind-merge-config";

export type { VariantProps } from "tailwind-variants";

export const tv = createTV({
  twMergeConfig: ANGEE_TW_MERGE_CONFIG,
});
