import type { ReactNode } from "react";
import type { Row } from "@angee/metadata";

import { PAGE_ELEMENT_SLOT } from "./types";

/**
 * `Tab` — one declared tab section. The owning page/form parser reads its props
 * and renders the tab strip + panel. Compose `Group`/`Field`/any content inside.
 * Like the other page
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
  /** Saved-record fields needed by this tab, selected even while it is hidden. */
  requiredFields?: readonly string[];
  /** Saved-record visibility; hidden until the record has loaded. */
  visibleWhen?: (record: Row) => boolean;
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
