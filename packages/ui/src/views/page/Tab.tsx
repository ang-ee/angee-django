import type { ReactNode } from "react";

import { PAGE_ELEMENT_SLOT } from "./types";

/**
 * `Tab` — one section of a `Notebook` (the form `Notebook`/`Tab` Element).
 * Render-less marker: the `Notebook` reads its props and renders the tab strip +
 * panel. Compose `Group`/`Field`/any content inside. Like the other page
 * elements it carries `[PAGE_ELEMENT_SLOT]`, so an addon can export reusable
 * `<Tab>` constants and `parsePageTabs` discovers them (flattening fragments and
 * asserting unique ids) wherever they are composed.
 */
export interface TabProps {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
  hidden?: boolean;
  children?: ReactNode;
}

/** A parsed tab is its props unchanged (cf. `ActionDescriptor`). */
export type TabDescriptor = TabProps;

function TabMarker(_props: TabProps): null {
  return null;
}

export const Tab = Object.assign(TabMarker, {
  [PAGE_ELEMENT_SLOT]: "tab" as const,
});
