import type { CoreContainer } from "../runtime";

/** The console shell's containers: notices, user-menu items and the docked drawers per edge. */
export const SHELL_CONTAINERS: readonly CoreContainer[] = [
  { address: "shell#notices" },
  { address: "shell#user-menu" },
  { address: "shell#drawers-right" },
  { address: "shell#drawers-bottom" },
];
