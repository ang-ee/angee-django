import { useAppRuntime, useContainer, type CoreContainer } from "../runtime";
import { DeveloperModeMenuItem } from "./DeveloperMode";
import { ViewAsBanner, ViewAsPicker } from "./ViewAs";

/**
 * The console shell's containers: notices, user-menu items and the docked
 * drawers per edge. The framework's own children (the view-as notice and
 * picker, the developer-mode switch) are every addon's to adjust.
 */
export const SHELL_CONTAINERS: readonly CoreContainer[] = [
  // The top bar's app menus and the breadcrumb strip under it (G-20); a layer hides one per route.
  { address: "shell#regions", children: { "chrome.app-menu": { content: null }, "chrome.breadcrumbs": { content: null } } },
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

/** Whether a `shell#regions` child shows on this page; outside a composed app every region shows. */
export function useShellRegion(id: string): boolean {
  const composed = Boolean(useAppRuntime().containers?.declared["shell#regions"]);
  const regions = useContainer("shell#regions");
  return !composed || regions.some((region) => region.id === id);
}
