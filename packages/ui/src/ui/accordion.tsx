import * as React from "react";
import { Accordion as BaseAccordion } from "@base-ui/react/accordion";
import type {
  AccordionHeaderProps as BaseAccordionHeaderProps,
  AccordionItemProps as BaseAccordionItemProps,
  AccordionPanelProps as BaseAccordionPanelProps,
  AccordionRootProps as BaseAccordionRootProps,
  AccordionTriggerProps as BaseAccordionTriggerProps,
} from "@base-ui/react/accordion";

import { Glyph } from "../chrome/Glyph";
import { createVariantContext } from "../lib/variant-context";
import { tv, type VariantProps } from "../lib/variants";

export const accordionVariants = tv({
  slots: {
    root: "w-full min-w-0",
    item: "border-b border-border-subtle",
    header: "m-0",
    trigger:
      "group flex w-full cursor-pointer items-center gap-2 text-left text-13 font-medium leading-5 text-fg outline-none transition-colors hover:text-fg focus-visible:focus-ring data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60",
    icon:
      "flex size-4 shrink-0 -rotate-90 items-center justify-center text-fg-muted transition-transform group-data-[panel-open]:rotate-0 [&_.glyph]:size-3.5",
    panel: "overflow-hidden text-13 leading-relaxed text-fg-2",
  },
  variants: {
    variant: {
      default: {
        trigger: "py-3",
        panel: "pb-3",
      },
      row: {
        root: "contents",
        item: "contents",
        header: "contents",
        trigger:
          "h-10 px-3 text-xs font-semibold text-fg-2 hover:bg-sheet-2",
        icon: "size-3 text-fg-subtle [&_.glyph]:size-3",
        panel: "contents",
      },
      flush: {
        item: "border-b-0",
        trigger: "py-2",
        panel: "pb-2",
      },
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

export type AccordionRecipeProps = VariantProps<typeof accordionVariants>;
export type AccordionVariant = NonNullable<AccordionRecipeProps["variant"]>;

const { Provider: AccordionVariantProvider, useVariant: useAccordionVariant } =
  createVariantContext<AccordionVariant>("default");

export type AccordionRootProps<Value = any> = Omit<
  BaseAccordionRootProps<Value>,
  "className"
> &
  Pick<AccordionRecipeProps, "variant"> & {
    className?: string;
  };

export const AccordionRoot = React.forwardRef<
  HTMLDivElement,
  AccordionRootProps
>(function AccordionRoot({ className, variant = "default", ...props }, ref) {
  const styles = accordionVariants({ variant });
  return (
    <AccordionVariantProvider value={variant}>
      <BaseAccordion.Root
        ref={ref}
        className={styles.root({ className })}
        data-variant={variant}
        {...props}
      />
    </AccordionVariantProvider>
  );
});
AccordionRoot.displayName = "AccordionRoot";

export type AccordionItemProps = Omit<
  BaseAccordionItemProps,
  "className"
> &
  Pick<AccordionRecipeProps, "variant"> & {
    className?: string;
  };

export const AccordionItem = React.forwardRef<
  HTMLDivElement,
  AccordionItemProps
>(function AccordionItem({ className, variant, ...props }, ref) {
  const resolvedVariant = useAccordionVariant(variant);
  const styles = accordionVariants({ variant: resolvedVariant });
  return (
    <BaseAccordion.Item
      ref={ref}
      className={styles.item({ className })}
      {...props}
    />
  );
});
AccordionItem.displayName = "AccordionItem";

export type AccordionHeaderProps = Omit<
  BaseAccordionHeaderProps,
  "className"
> &
  Pick<AccordionRecipeProps, "variant"> & {
    className?: string;
  };

export const AccordionHeader = React.forwardRef<
  HTMLHeadingElement,
  AccordionHeaderProps
>(function AccordionHeader({ className, variant, ...props }, ref) {
  const resolvedVariant = useAccordionVariant(variant);
  const styles = accordionVariants({ variant: resolvedVariant });
  return (
    <BaseAccordion.Header
      ref={ref}
      className={styles.header({ className })}
      {...props}
    />
  );
});
AccordionHeader.displayName = "AccordionHeader";

export type AccordionTriggerProps = Omit<
  BaseAccordionTriggerProps,
  "className"
> &
  Pick<AccordionRecipeProps, "variant"> & {
    className?: string;
  };

export const AccordionTrigger = React.forwardRef<
  HTMLElement,
  AccordionTriggerProps
>(function AccordionTrigger({ className, variant, ...props }, ref) {
  const resolvedVariant = useAccordionVariant(variant);
  const styles = accordionVariants({ variant: resolvedVariant });
  return (
    <BaseAccordion.Trigger
      ref={ref}
      className={styles.trigger({ className })}
      {...props}
    />
  );
});
AccordionTrigger.displayName = "AccordionTrigger";

export type AccordionIconProps = React.HTMLAttributes<HTMLSpanElement> &
  Pick<AccordionRecipeProps, "variant"> & {
    className?: string;
  };

export const AccordionIcon = React.forwardRef<
  HTMLSpanElement,
  AccordionIconProps
>(function AccordionIcon({ className, children, variant, ...props }, ref) {
  const resolvedVariant = useAccordionVariant(variant);
  const styles = accordionVariants({ variant: resolvedVariant });
  return (
    <span ref={ref} className={styles.icon({ className })} {...props}>
      {children ?? <Glyph name="chevron-down" />}
    </span>
  );
});
AccordionIcon.displayName = "AccordionIcon";

export type AccordionPanelProps = Omit<
  BaseAccordionPanelProps,
  "className"
> &
  Pick<AccordionRecipeProps, "variant"> & {
    className?: string;
  };

export const AccordionPanel = React.forwardRef<
  HTMLDivElement,
  AccordionPanelProps
>(function AccordionPanel({ className, variant, ...props }, ref) {
  const resolvedVariant = useAccordionVariant(variant);
  const styles = accordionVariants({ variant: resolvedVariant });
  return (
    <BaseAccordion.Panel
      ref={ref}
      className={styles.panel({ className })}
      {...props}
    />
  );
});
AccordionPanel.displayName = "AccordionPanel";

export const Accordion = Object.assign(AccordionRoot, {
  Root: AccordionRoot,
  Item: AccordionItem,
  Header: AccordionHeader,
  Trigger: AccordionTrigger,
  Icon: AccordionIcon,
  Panel: AccordionPanel,
});
