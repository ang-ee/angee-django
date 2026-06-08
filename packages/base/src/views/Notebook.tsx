import {
  Children,
  isValidElement,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { Tabs } from "../ui/tabs";

/**
 * `Tab` — one section of a `Notebook`. Render-less marker: the `Notebook`
 * reads its props and renders the tab + panel. Compose `Group`/`Field`/any
 * content inside.
 */
export interface TabProps {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
  hidden?: boolean;
  children?: ReactNode;
}

export function Tab({ children }: TabProps): ReactElement {
  return <>{children}</>;
}
Object.assign(Tab, { $$notebookTab: true });

/**
 * `Notebook` — the tabbed record-section container (the form `Notebook`/`Tab`
 * Element). Folds `<Tab>` children into one `Tabs` band of sections. Active tab
 * is uncontrolled by default (seeded from `defaultTab`/the first tab); pass
 * `value`/`onValueChange` to control it (e.g. URL-synced from the page).
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
    () =>
      Children.toArray(children).filter(
        (child): child is ReactElement<TabProps> =>
          isValidElement(child) &&
          (child.type as { $$notebookTab?: boolean }).$$notebookTab === true &&
          !(child.props as TabProps).hidden,
      ),
    [children],
  );
  const firstTab = tabs[0]?.props.id ?? "";
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
          <Tabs.Tab key={tab.props.id} value={tab.props.id}>
            {tab.props.icon}
            {tab.props.label}
            {tab.props.badge !== undefined ? (
              <Tabs.Count>{tab.props.badge}</Tabs.Count>
            ) : null}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {tabs.map((tab) => (
        <Tabs.Panel key={tab.props.id} value={tab.props.id} className="pt-4">
          {tab.props.children}
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}
