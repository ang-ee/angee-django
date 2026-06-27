import * as React from "react";
import { Collapsible as BaseCollapsible } from "@base-ui/react/collapsible";
import type {
  CollapsiblePanelProps as BaseCollapsiblePanelProps,
  CollapsibleRootProps as BaseCollapsibleRootProps,
  CollapsibleTriggerProps as BaseCollapsibleTriggerProps,
} from "@base-ui/react/collapsible";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { createVariantContext } from "../lib/variant-context";
import { tv, type VariantProps } from "../lib/variants";
import { textRoleVariants } from "./text";
import { interactiveSurface } from "./widget-control";

export const collapsibleVariants = tv({
  slots: {
    root: "w-full min-w-0",
    trigger: `group inline-flex cursor-pointer items-center gap-2 text-left text-13 font-medium text-fg hover:text-fg ${interactiveSurface(
      { focus: "visible", disabled: "pseudo" },
    )}`,
    icon:
      "flex size-4 shrink-0 -rotate-90 items-center justify-center text-fg-muted transition-transform group-data-[panel-open]:rotate-0 [&_.glyph]:size-3.5",
    panel: cn(textRoleVariants({ role: "description" }), "overflow-hidden leading-relaxed"),
  },
  variants: {
    variant: {
      default: {
        trigger: "py-2",
        panel: "pt-1",
      },
      row: {
        root: "contents",
        trigger:
          "w-3 justify-center text-fg-subtle hover:text-fg-muted [&_.glyph]:size-3",
        icon: "size-3 [&_.glyph]:size-3",
        panel: "contents",
      },
      section: {
        trigger: "h-7 rounded-6 px-2 text-fg-muted hover:bg-inset hover:text-fg",
        panel: "pt-3",
      },
      flush: {
        trigger: "py-1.5",
        panel: "pt-1",
      },
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

export type CollapsibleRecipeProps = VariantProps<
  typeof collapsibleVariants
>;
export type CollapsibleVariant =
  NonNullable<CollapsibleRecipeProps["variant"]>;

const {
  Provider: CollapsibleVariantProvider,
  useVariant: useCollapsibleVariant,
} = createVariantContext<CollapsibleVariant>("default");

export type CollapsibleRootProps = Omit<
  BaseCollapsibleRootProps,
  "className"
> &
  Pick<CollapsibleRecipeProps, "variant"> & {
    className?: string;
  };

export const CollapsibleRoot = React.forwardRef<
  HTMLDivElement,
  CollapsibleRootProps
>(function CollapsibleRoot(
  { className, variant = "default", ...props },
  ref,
) {
  const styles = collapsibleVariants({ variant });
  return (
    <CollapsibleVariantProvider value={variant}>
      <BaseCollapsible.Root
        ref={ref}
        className={styles.root({ className })}
        data-variant={variant}
        {...props}
      />
    </CollapsibleVariantProvider>
  );
});
CollapsibleRoot.displayName = "CollapsibleRoot";

export type CollapsibleTriggerProps = Omit<
  BaseCollapsibleTriggerProps,
  "className"
> &
  Pick<CollapsibleRecipeProps, "variant"> & {
    className?: string;
  };

export const CollapsibleTrigger = React.forwardRef<
  HTMLButtonElement,
  CollapsibleTriggerProps
>(function CollapsibleTrigger({ className, variant, ...props }, ref) {
  const resolvedVariant = useCollapsibleVariant(variant);
  const styles = collapsibleVariants({ variant: resolvedVariant });
  return (
    <BaseCollapsible.Trigger
      ref={ref}
      className={styles.trigger({ className })}
      {...props}
    />
  );
});
CollapsibleTrigger.displayName = "CollapsibleTrigger";

export type CollapsibleIconProps = React.HTMLAttributes<HTMLSpanElement> &
  Pick<CollapsibleRecipeProps, "variant"> & {
    className?: string;
  };

export const CollapsibleIcon = React.forwardRef<
  HTMLSpanElement,
  CollapsibleIconProps
>(function CollapsibleIcon({ className, children, variant, ...props }, ref) {
  const resolvedVariant = useCollapsibleVariant(variant);
  const styles = collapsibleVariants({ variant: resolvedVariant });
  return (
    <span ref={ref} className={styles.icon({ className })} {...props}>
      {children ?? <Glyph name="chevron-down" />}
    </span>
  );
});
CollapsibleIcon.displayName = "CollapsibleIcon";

export type CollapsiblePanelProps = Omit<
  BaseCollapsiblePanelProps,
  "className"
> &
  Pick<CollapsibleRecipeProps, "variant"> & {
    className?: string;
  };

export const CollapsiblePanel = React.forwardRef<
  HTMLDivElement,
  CollapsiblePanelProps
>(function CollapsiblePanel({ className, variant, ...props }, ref) {
  const resolvedVariant = useCollapsibleVariant(variant);
  const styles = collapsibleVariants({ variant: resolvedVariant });
  return (
    <BaseCollapsible.Panel
      ref={ref}
      className={styles.panel({ className })}
      {...props}
    />
  );
});
CollapsiblePanel.displayName = "CollapsiblePanel";

export const Collapsible = Object.assign(CollapsibleRoot, {
  Root: CollapsibleRoot,
  Trigger: CollapsibleTrigger,
  Icon: CollapsibleIcon,
  Panel: CollapsiblePanel,
});
