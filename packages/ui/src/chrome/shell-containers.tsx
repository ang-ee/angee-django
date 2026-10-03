import type { CoreContainer } from "../runtime";
import { DeveloperModeMenuItem } from "./DeveloperMode";
import { ViewAsBanner, ViewAsPicker } from "./ViewAs";

/**
 * The console shell's containers: notices, user-menu items and the docked
 * drawers per edge. The framework's own children (the view-as notice and
 * picker, the developer-mode switch) are every addon's to adjust.
 */
export const SHELL_CONTAINERS: readonly CoreContainer[] = [
  { address: "shell#notices", children: { "chrome.view-as": { sequence: 20, content: <ViewAsBanner /> } } },
  {
    address: "shell#user-menu",
    children: {
      "chrome.view-as": { sequence: 20, content: <ViewAsPicker /> },
      "chrome.developer-mode": { sequence: 90, content: <DeveloperModeMenuItem /> },
    },
  },
  { address: "shell#drawers-right" },
  { address: "shell#drawers-bottom" },
];
