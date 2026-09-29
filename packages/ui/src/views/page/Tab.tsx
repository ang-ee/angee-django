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
export interface TabLabelKey {
  namespace: string;
  key: string;
  /** English from the addon's own message bundle for provider-less previews. */
  fallback?: string;
}

export type TabLabel = ReactNode | TabLabelKey;

export function resolveTabLabel(label: TabLabel, i18n: { getFixedT: (language: null, namespace: string) => (key: string, options?: { defaultValue?: string }) => unknown } | null): ReactNode {
  if (label && typeof label === "object" && !Array.isArray(label) && "namespace" in label && "key" in label) {
    const value = label as TabLabelKey;
    const translated = i18n?.getFixedT(null, value.namespace)(value.key, { defaultValue: value.fallback ?? value.key });
    return translated == null ? value.fallback ?? value.key : String(translated);
  }
  return label as ReactNode;
}

export interface TabProps {
  id: string;
  label: TabLabel;
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
