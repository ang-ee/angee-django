import type { CoreContainer } from "../runtime";

/**
 * The context aside's container: `record#aside` children are strip tabs and
 * drill-ins contributed by addons, plus the tabs a page publishes at render time.
 * The framework declares no tab of its own; the timeline addon owns the first.
 */
export const CHATTER_CONTAINERS: readonly CoreContainer[] = [
  {
    address: "record#aside",
    models: true,
    // Pages publish their own tabs at render time; `only` may name them.
    extras: true,
  },
];
