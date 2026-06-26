import * as React from "react";

import { cn } from "../lib/cn";
import { NavLink } from "../ui/nav-link";
import { Tabs, type TabsVariant } from "../ui/tabs";

type NavigateHandler = (href: string) => void;

export interface SectionTabItem {
  count?: React.ReactNode;
  disabled?: boolean;
  href?: string;
  icon?: React.ReactNode;
  id: string;
  label: React.ReactNode;
}

export interface SectionTabsProps {
  className?: string;
  defaultValue?: string;
  items: readonly SectionTabItem[];
  listClassName?: string;
  onNavigate?: NavigateHandler;
  onValueChange?: (value: string) => void;
  value?: string;
  variant?: Extract<TabsVariant, "card" | "page" | "pill">;
}

export const SectionTabs = React.forwardRef<HTMLDivElement, SectionTabsProps>(
  function SectionTabs(
    {
      className,
      defaultValue,
      items,
      listClassName,
      onNavigate,
      onValueChange,
      value,
      variant = "pill",
    },
    ref,
  ) {
    const selected = value ?? defaultValue ?? items[0]?.id;

    return (
      <Tabs
        ref={ref}
        className={className}
        defaultValue={defaultValue ?? selected}
        onValueChange={onValueChange}
        value={value}
        variant={variant}
      >
        <Tabs.List className={cn("min-w-0", listClassName)}>
          {items.map((item) => (
            <Tabs.Tab
              key={item.id}
              disabled={item.disabled}
              icon={item.icon}
              nativeButton={item.href ? false : undefined}
              render={
                item.href ? (
                  <NavLink
                    active={item.id === selected}
                    disabled={item.disabled}
                    href={item.href}
                    onNavigate={onNavigate}
                  />
                ) : undefined
              }
              value={item.id}
            >
              {item.label}
              {item.count !== undefined ? <Tabs.Count>{item.count}</Tabs.Count> : null}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
    );
  },
);
SectionTabs.displayName = "SectionTabs";
