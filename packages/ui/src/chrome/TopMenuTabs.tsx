import type { ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Tabs } from "../ui/tabs";
import { Glyph } from "./Glyph";

/**
 * A presentational collection-view tab. The framework owns the tab strip and
 * its `?tab=` query state; the product owns what each tab means.
 */
export interface TopMenuTab {
  id: string;
  label: string;
  icon?: string;
}

export interface TopMenuTabsProps {
  className?: string;
  tabs: readonly TopMenuTab[];
}

export function TopMenuTabs({
  className,
  tabs,
}: TopMenuTabsProps): ReactElement | null {
  const t = useUiT();
  const [rawTab, setActiveTab] = useQueryState("tab", parseAsString);
  const [firstTab] = tabs;
  if (!firstTab) return null;

  const activeId = tabs.some((tab) => tab.id === rawTab) ? rawTab : firstTab.id;

  return (
    <Tabs
      variant="rail"
      value={activeId}
      onValueChange={(value: string | null) => {
        if (value != null) void setActiveTab(String(value));
      }}
      className={cn("min-w-0", className)}
    >
      <Tabs.List aria-label={t("chrome.collectionViews")}>
        {tabs.map((tab) => (
          <Tabs.Tab
            key={tab.id}
            value={tab.id}
            icon={tab.icon ? (
              <Glyph name={tab.icon} size={14} className="shrink-0" />
            ) : undefined}
          >
            <span className="truncate">{tab.label}</span>
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs>
  );
}
