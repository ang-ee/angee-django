import type { ReactElement } from "react";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { cn } from "../lib/cn";
import { useDataViewMaybe, type DataViewFilter } from "../views";

const TOP_TAB_IDS = ["all", "starred", "archive"] as const;
export type TopMenuTabId = (typeof TOP_TAB_IDS)[number];

export interface TopMenuTab {
  id: TopMenuTabId;
  label: string;
  filter: DataViewFilter;
}

const DEFAULT_TABS: readonly TopMenuTab[] = [
  { id: "all", label: "All notes", filter: {} },
  { id: "starred", label: "Starred", filter: { isStarred: true } },
  { id: "archive", label: "Archive", filter: { status: { exact: "ARCHIVED" } } },
];

const tabClass =
  "inline-flex h-8 items-center rounded-md px-3 text-13 font-medium text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring aria-selected:bg-rail-hi aria-selected:text-on-rail-hi";

export interface TopMenuProps {
  className?: string;
  tabs?: readonly TopMenuTab[];
}

export function TopMenu({ className, tabs = DEFAULT_TABS }: TopMenuProps): ReactElement | null {
  const dataView = useDataViewMaybe();
  const [activeTab, setActiveTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TOP_TAB_IDS).withDefault("all"),
  );

  return (
    <div
      role="tablist"
      aria-label="Collection views"
      className={cn("flex min-w-0 gap-1", className)}
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={activeTab === tab.id}
          className={tabClass}
          onClick={() => {
            void setActiveTab(tab.id);
            dataView?.setFilter(tab.filter);
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
