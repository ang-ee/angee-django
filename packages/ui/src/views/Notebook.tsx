import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import { Tabs } from "../ui/tabs";
import { parsePageTabs } from "./page";

/**
 * `Notebook` — the tabbed record-section container (the form `Notebook`/`Tab`
 * Element). Folds `<Tab>` page elements into one `Tabs` band of sections via
 * `parsePageTabs` (the shared page-element parser — fragment-flattening, unique
 * ids), so a tab composes like any other page element. Active tab is uncontrolled
 * by default (seeded from `defaultTab`/the first tab); pass `value`/`onValueChange`
 * to control it (e.g. URL-synced from the page).
 */
export interface NotebookProps {
  children?: ReactNode;
  defaultTab?: string;
  value?: string;
  onValueChange?: (value: string) => void;
  className?: string;
}

export function Notebook({
  children,
  defaultTab,
  value,
  onValueChange,
  className,
}: NotebookProps): ReactElement {
  const tabs = useMemo(
    () => parsePageTabs(children).filter((tab) => !tab.hidden),
    [children],
  );
  const firstTab = tabs[0]?.id ?? "";
  const [internal, setInternal] = useState(defaultTab ?? firstTab);
  const active = value ?? internal;

  return (
    <Tabs
      variant="page"
      value={active}
      onValueChange={(next) => {
        if (value === undefined) setInternal(next);
        onValueChange?.(next);
      }}
      className={className}
    >
      <Tabs.List>
        {tabs.map((tab) => (
          <Tabs.Tab key={tab.id} value={tab.id}>
            {tab.icon}
            {tab.label}
            {tab.badge !== undefined ? <Tabs.Count>{tab.badge}</Tabs.Count> : null}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {tabs.map((tab) => (
        <Tabs.Panel key={tab.id} value={tab.id} className="pt-4">
          {tab.children}
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}
