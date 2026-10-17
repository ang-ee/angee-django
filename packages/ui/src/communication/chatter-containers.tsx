import type { CoreContainer } from "../runtime";

/** Record aside tabs are contributed by the addons that implement them. */
export const CHATTER_CONTAINERS: readonly CoreContainer[] = [{
  address: "record#aside",
  models: true,
  // Pages publish their own tabs at render time; `only` may name them.
  extras: true,
}];
