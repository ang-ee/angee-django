import type { CoreContainer } from "../../runtime";

/**
 * The record form's containers. `form#…` children show on every model's form;
 * `<model>#…` children show on that model and its MTI children. Record verbs go
 * in `#actions` (the toolbar) or `#actions-menu` (the overflow menu); a
 * bridge's per-implementation verb is a `variant` of the verb it specialises.
 */
export const FORM_CONTAINERS: readonly CoreContainer[] = [
  { address: "form#sections", models: true },
  { address: "form#rail", models: true },
  { address: "form#actions", models: true },
  { address: "form#actions-menu", models: true },
  { address: "form#chrome", models: true },
];
