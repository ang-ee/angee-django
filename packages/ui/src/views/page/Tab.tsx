import type { ReactNode } from "react";
import type { DataResourceMetadata, Row } from "@angee/metadata";

import { PAGE_ELEMENT_SLOT } from "./types";
import { resolveLabelKey, type LabelKey, type LabelKeyResolver } from "../../lib/labels";

/**
 * `Tab` — one declared tab section. The owning page/form parser reads its props
 * and renders the tab strip + panel. Compose `Group`/`Field`/any content inside.
 * Like the other page
 * elements it carries `[PAGE_ELEMENT_SLOT]`, so an addon can export reusable
 * `<Tab>` constants and `parsePageTabs` discovers them (flattening fragments and
 * asserting unique ids) wherever they are composed.
 */
export type TabLabelKey = LabelKey;

export type TabLabel = ReactNode | TabLabelKey;

export function resolveTabLabel(label: TabLabel, i18n: LabelKeyResolver | null): ReactNode {
  return resolveLabelKey(label, i18n);
}

export interface TabProps {
  id: string;
  label: TabLabel;
  icon?: ReactNode;
  badge?: ReactNode;
  hidden?: boolean;
  /** Saved-record fields needed by this tab, selected even while it is hidden. */
  requiredFields?: readonly string[];
  /**
   * Saved-record visibility; hidden until the record has loaded. The second
   * argument is the form's data resource, so a tab contributed to every form
   * can show only where the resource declares what it reads (`history`).
   */
  visibleWhen?: (record: Row, resource: DataResourceMetadata | null) => boolean;
  children?: ReactNode;
}

/** A parsed tab is its props unchanged (cf. `ActionDescriptor`). */
export type TabDescriptor = TabProps;

function TabMarker(_props: TabProps): null {
  return null;
}

/**
 * @kind contribution
 */
export const Tab = Object.assign(TabMarker, {
  [PAGE_ELEMENT_SLOT]: "tab" as const,
});
