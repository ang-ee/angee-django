import * as React from "react";
import { Tabs as BaseTabs } from "@base-ui/react/tabs";
import type {
  TabsIndicatorProps as BaseTabsIndicatorProps,
  TabsListProps as BaseTabsListProps,
  TabsPanelProps as BaseTabsPanelProps,
  TabsRootProps as BaseTabsRootProps,
  TabsTabProps as BaseTabsTabProps,
} from "@base-ui/react/tabs";

import { createVariantContext } from "../lib/variant-context";
import { tv, type VariantProps } from "../lib/variants";

export const tabsVariants = tv({
  slots: {
    root: "w-full min-w-0",
    list: "flex min-w-0",
    tab:
      "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 text-13 font-medium text-fg-muted outline-none transition-colors hover:text-fg focus-visible:focus-ring data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[active]:text-fg [&_.glyph]:size-3.5",
    indicator: "absolute bg-brand",
    panel: "focus:outline-none",
    count:
      "inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-inset px-1.5 text-2xs font-semibold tabular-nums text-fg-muted",
  },
  variants: {
    variant: {
      card: {
        list: "relative gap-0 border-b border-border-subtle",
        tab: "-mb-px h-9 border-b-2 border-transparent px-3 data-[active]:border-brand",
        indicator: "bottom-0 h-0.5",
        panel: "pt-3",
      },
      page: {
        list: "relative gap-0 border-b border-border-subtle bg-sheet px-4",
        tab: "-mb-px h-10 border-b-2 border-transparent px-4 data-[active]:border-brand",
        indicator: "bottom-0 h-0.5",
        panel: "pt-4",
      },
      pill: {
        list: "gap-1 rounded-md bg-inset p-1",
        tab: "h-7 rounded border border-transparent px-2.5 data-[active]:bg-sheet data-[active]:shadow-xs",
        indicator: "hidden",
        panel: "pt-3",
      },
    },
  },
  defaultVariants: {
    variant: "card",
  },
});

type TabsRecipeProps = VariantProps<typeof tabsVariants>;

export type TabsVariant = NonNullable<TabsRecipeProps["variant"]>;

const { Provider: TabsVariantProvider, useVariant: useTabsVariant } =
  createVariantContext<TabsVariant>("card");

export type TabsRootProps = Omit<BaseTabsRootProps, "className"> &
  Pick<TabsRecipeProps, "variant"> & {
    className?: string;
  };

export const TabsRoot = React.forwardRef<HTMLDivElement, TabsRootProps>(
  function TabsRoot({ className, variant = "card", ...props }, ref) {
    const styles = tabsVariants({ variant });
    return (
      <TabsVariantProvider value={variant}>
        <BaseTabs.Root
          ref={ref}
          className={styles.root({ className })}
          data-variant={variant}
          {...props}
        />
      </TabsVariantProvider>
    );
  },
);
TabsRoot.displayName = "TabsRoot";

export type TabsListProps = Omit<BaseTabsListProps, "className"> &
  Pick<TabsRecipeProps, "variant"> & {
    className?: string;
  };

export const TabsList = React.forwardRef<HTMLDivElement, TabsListProps>(
  function TabsList({ className, variant, ...props }, ref) {
    const resolvedVariant = useTabsVariant(variant);
    const styles = tabsVariants({ variant: resolvedVariant });
    return (
      <BaseTabs.List
        ref={ref}
        className={styles.list({ className })}
        {...props}
      />
    );
  },
);
TabsList.displayName = "TabsList";

export type TabsTabProps = Omit<BaseTabsTabProps, "className" | "render"> &
  Pick<TabsRecipeProps, "variant"> & {
    className?: string;
    icon?: React.ReactNode;
    render?: BaseTabsTabProps["render"];
  };

export const TabsTab = React.forwardRef<HTMLElement, TabsTabProps>(
  function TabsTab({ className, icon, children, variant, ...props }, ref) {
    const resolvedVariant = useTabsVariant(variant);
    const styles = tabsVariants({ variant: resolvedVariant });
    return (
      <BaseTabs.Tab
        ref={ref}
        className={styles.tab({ className })}
        {...props}
      >
        {icon ?? null}
        {children}
      </BaseTabs.Tab>
    );
  },
);
TabsTab.displayName = "TabsTab";

export type TabsIndicatorProps = Omit<
  BaseTabsIndicatorProps,
  "className"
> &
  Pick<TabsRecipeProps, "variant"> & {
    className?: string;
  };

export const TabsIndicator = React.forwardRef<
  HTMLSpanElement,
  TabsIndicatorProps
>(function TabsIndicator({ className, variant, ...props }, ref) {
  const resolvedVariant = useTabsVariant(variant);
  const styles = tabsVariants({ variant: resolvedVariant });
  return (
    <BaseTabs.Indicator
      ref={ref}
      className={styles.indicator({ className })}
      {...props}
    />
  );
});
TabsIndicator.displayName = "TabsIndicator";

export type TabsPanelProps = Omit<BaseTabsPanelProps, "className"> &
  Pick<TabsRecipeProps, "variant"> & {
    className?: string;
  };

export const TabsPanel = React.forwardRef<HTMLDivElement, TabsPanelProps>(
  function TabsPanel({ className, variant, ...props }, ref) {
    const resolvedVariant = useTabsVariant(variant);
    const styles = tabsVariants({ variant: resolvedVariant });
    return (
      <BaseTabs.Panel
        ref={ref}
        className={styles.panel({ className })}
        {...props}
      />
    );
  },
);
TabsPanel.displayName = "TabsPanel";

export type TabsCountProps = React.HTMLAttributes<HTMLSpanElement> & {
  className?: string;
};

export const TabsCount = React.forwardRef<HTMLSpanElement, TabsCountProps>(
  function TabsCount({ className, ...props }, ref) {
    const styles = tabsVariants();
    return (
      <span ref={ref} className={styles.count({ className })} {...props} />
    );
  },
);
TabsCount.displayName = "TabsCount";

export const Tabs = Object.assign(TabsRoot, {
  List: TabsList,
  Tab: TabsTab,
  Indicator: TabsIndicator,
  Panel: TabsPanel,
  Count: TabsCount,
});
