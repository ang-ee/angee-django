import * as React from "react";
import { Popover as BasePopover } from "@base-ui/react/popover";
import type {
  PopoverArrowProps as BasePopoverArrowProps,
  PopoverBackdropProps as BasePopoverBackdropProps,
  PopoverCloseProps as BasePopoverCloseProps,
  PopoverDescriptionProps as BasePopoverDescriptionProps,
  PopoverPortalProps as BasePopoverPortalProps,
  PopoverPopupProps as BasePopoverPopupProps,
  PopoverPositionerProps as BasePopoverPositionerProps,
  PopoverRootProps as BasePopoverRootProps,
  PopoverTitleProps as BasePopoverTitleProps,
  PopoverTriggerProps as BasePopoverTriggerProps,
} from "@base-ui/react/popover";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

/** Global layer for the fixed Base UI positioner that owns portal stacking.
 * @kind token
 */
export const PORTALED_CONTROL_LAYER = "z-popover";

// A positioning anchor described by a rect instead of a DOM node. Matches the
// structural shape the positioner accepts for its `anchor` prop.
type VirtualElement = {
  getBoundingClientRect(): DOMRect;
  getClientRects?(): DOMRect[] | DOMRectList;
  contextElement?: Element;
};

/**
 * @kind token
 */
export const popoverVariants = tv({
  slots: {
    // Fade + subtle scale on enter/exit; Tailwind 4 emits `scale` for scale-95.
    content:
      "overflow-hidden rounded-8 border border-border-subtle bg-popover shadow-popover outline-none motion-safe:transition-[opacity,scale] data-[starting-style]:opacity-0 data-[starting-style]:scale-95 data-[ending-style]:opacity-0 data-[ending-style]:scale-95",
    list: "max-h-64 overflow-y-auto p-1",
    item:
      "flex h-7 cursor-pointer select-none items-center gap-2 rounded-6 px-2 text-13 text-fg outline-none data-[highlighted]:bg-inset data-[selected]:text-brand-soft-text",
    modal:
      "z-modal overflow-hidden rounded-12 border border-border-subtle bg-sheet shadow-lg outline-none",
    modalBackdrop: "fixed inset-0 z-modal-backdrop bg-overlay",
    title: "px-3 py-2 text-13 font-semibold text-fg",
    description: "px-3 pb-2 text-12 text-fg-muted",
  },
  variants: {
    surface: {
      default: { content: "bg-popover" },
      sheet: { content: "bg-sheet" },
    },
  },
  defaultVariants: {
    surface: "default",
  },
});

/**
 * @kind token
 */
export const POPUP_BASE = popoverVariants().content();
/**
 * @kind token
 */
export const POPUP_ITEM = popoverVariants().item();
/**
 * @kind token
 */
export const POPUP_LIST = popoverVariants().list();

type PopoverRecipeProps = VariantProps<typeof popoverVariants>;
export type PopoverRootProps<Payload = unknown> =
  BasePopoverRootProps<Payload>;
export type PopoverTriggerProps<Payload = unknown> =
  BasePopoverTriggerProps<Payload>;
export type PopoverPortalProps = BasePopoverPortalProps;
export type PopoverPositionerProps = Omit<
  BasePopoverPositionerProps,
  "className"
> & {
  className?: string;
};
export type PopoverArrowProps = BasePopoverArrowProps;
export type PopoverBackdropProps = BasePopoverBackdropProps;
export type PopoverCloseProps = BasePopoverCloseProps;

/**
 * @owner Popover
 */
export const PopoverRoot = BasePopover.Root;
/**
 * @owner Popover
 */
export const PopoverPortal = BasePopover.Portal;
/**
 * @owner Popover
 */
export const PopoverArrow = BasePopover.Arrow;
/**
 * @owner Popover
 */
export const PopoverBackdrop = BasePopover.Backdrop;
/**
 * @owner Popover
 */
export const PopoverClose = BasePopover.Close;
/**
 * @owner Popover
 */
export const PopoverViewport = BasePopover.Viewport;

// Thin re-export. Base UI's controlled `open`/`onOpenChange` owns the open/close
// transition; we no longer intercept the press here. The old rc.0-era capture-phase
// defer (swallow the trusted press, re-dispatch via `setTimeout`) is gone — heavy
// view-state work is de-prioritized at its owner (a React transition in the resource-view
// store), not by re-timing the trigger.
/**
 * @owner Popover
 */
export const PopoverTrigger = BasePopover.Trigger;

/**
 * @owner Popover
 */
export const PopoverPositioner = React.forwardRef<
  HTMLDivElement,
  PopoverPositionerProps
>(function PopoverPositioner({ className, ...props }, ref) {
  return (
    <BasePopover.Positioner
      ref={ref}
      className={cn(PORTALED_CONTROL_LAYER, className)}
      {...props}
    />
  );
});
PopoverPositioner.displayName = "PopoverPositioner";

export interface PopoverVirtualAnchorRect {
  x: number;
  y: number;
  width?: number;
  height?: number;
  contextElement?: Element | null;
}

export interface PopoverVirtualAnchorProps
  extends Omit<PopoverPositionerProps, "anchor">,
    PopoverVirtualAnchorRect {}

function popoverVirtualRect({
  x,
  y,
  width = 0,
  height = 0,
}: PopoverVirtualAnchorRect): DOMRect {
  return DOMRect.fromRect({ x, y, width, height });
}

/**
 * @owner Popover
 */
export function createPopoverVirtualAnchor(
  rect: PopoverVirtualAnchorRect,
): VirtualElement {
  const anchor: VirtualElement = {
    getBoundingClientRect() {
      return popoverVirtualRect(rect);
    },
    getClientRects() {
      return [popoverVirtualRect(rect)];
    },
  };
  if (rect.contextElement) {
    anchor.contextElement = rect.contextElement;
  }
  return anchor;
}

/**
 * @owner Popover
 */
export function usePopoverVirtualAnchor(
  rect: PopoverVirtualAnchorRect,
): VirtualElement {
  const { x, y, width = 0, height = 0, contextElement = null } = rect;
  return React.useMemo(
    () => createPopoverVirtualAnchor({ x, y, width, height, contextElement }),
    [contextElement, height, width, x, y],
  );
}

/**
 * @owner Popover
 */
export const PopoverVirtualAnchor = React.forwardRef<
  HTMLDivElement,
  PopoverVirtualAnchorProps
>(function PopoverVirtualAnchor(
  { x, y, width = 0, height = 0, contextElement = null, ...props },
  ref,
) {
  const anchor = usePopoverVirtualAnchor({
    x,
    y,
    width,
    height,
    contextElement,
  });
  return <PopoverPositioner ref={ref} anchor={anchor} {...props} />;
});
PopoverVirtualAnchor.displayName = "PopoverVirtualAnchor";

export type PopoverContentProps = BasePopoverPopupProps &
  Pick<PopoverRecipeProps, "surface"> & {
    className?: string;
  };

/**
 * @owner Popover
 */
export const PopoverContent = React.forwardRef<
  HTMLDivElement,
  PopoverContentProps
>(function PopoverContent({ className, surface = "default", ...props }, ref) {
  const styles = popoverVariants({ surface });
  return (
    <BasePopover.Popup
      ref={ref}
      className={styles.content({ className })}
      {...props}
    />
  );
});
PopoverContent.displayName = "PopoverContent";

/**
 * @owner Popover
 */
export const PopoverPopup = PopoverContent;

export type PopoverTitleProps = BasePopoverTitleProps & {
  className?: string;
};

/**
 * @owner Popover
 */
export const PopoverTitle = React.forwardRef<
  HTMLParagraphElement,
  PopoverTitleProps
>(function PopoverTitle({ className, ...props }, ref) {
  const styles = popoverVariants();
  return (
    <BasePopover.Title
      ref={ref}
      className={styles.title({ className })}
      {...props}
    />
  );
});
PopoverTitle.displayName = "PopoverTitle";

export type PopoverDescriptionProps = BasePopoverDescriptionProps & {
  className?: string;
};

/**
 * @owner Popover
 */
export const PopoverDescription = React.forwardRef<
  HTMLParagraphElement,
  PopoverDescriptionProps
>(function PopoverDescription({ className, ...props }, ref) {
  const styles = popoverVariants();
  return (
    <BasePopover.Description
      ref={ref}
      className={styles.description({ className })}
      {...props}
    />
  );
});
PopoverDescription.displayName = "PopoverDescription";

export type PopoverListProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

/**
 * @owner Popover
 */
export const PopoverList = React.forwardRef<HTMLDivElement, PopoverListProps>(
  function PopoverList({ className, ...props }, ref) {
    const styles = popoverVariants();
    return <div ref={ref} className={styles.list({ className })} {...props} />;
  },
);
PopoverList.displayName = "PopoverList";

export type PopoverItemProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

/**
 * @owner Popover
 */
export const PopoverItem = React.forwardRef<HTMLDivElement, PopoverItemProps>(
  function PopoverItem({ className, ...props }, ref) {
    const styles = popoverVariants();
    return <div ref={ref} className={styles.item({ className })} {...props} />;
  },
);
PopoverItem.displayName = "PopoverItem";

/**
 * @kind primitive
 */
export const Popover = {
  Root: PopoverRoot,
  Trigger: PopoverTrigger,
  Portal: PopoverPortal,
  Positioner: PopoverPositioner,
  VirtualAnchor: PopoverVirtualAnchor,
  Content: PopoverContent,
  Popup: PopoverPopup,
  List: PopoverList,
  Item: PopoverItem,
  Arrow: PopoverArrow,
  Backdrop: PopoverBackdrop,
  Title: PopoverTitle,
  Description: PopoverDescription,
  Close: PopoverClose,
  Viewport: PopoverViewport,
};
