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

import { tv, type VariantProps } from "../lib/variants";

// A positioning anchor described by a rect instead of a DOM node. Matches the
// structural shape the positioner accepts for its `anchor` prop.
type VirtualElement = {
  getBoundingClientRect(): DOMRect;
  getClientRects?(): DOMRect[] | DOMRectList;
  contextElement?: Element;
};

export const popoverVariants = tv({
  slots: {
    content:
      "z-popover overflow-hidden rounded-lg border border-border-subtle bg-popover shadow-popover outline-none data-[ending-style]:opacity-0 data-[starting-style]:opacity-0",
    list: "max-h-64 overflow-y-auto p-1",
    input:
      "h-7 w-full border-0 bg-transparent px-2 text-13 text-fg outline-none placeholder:text-fg-muted",
    item:
      "flex h-7 cursor-pointer select-none items-center gap-2 rounded-md px-2 text-13 text-fg outline-none data-[highlighted]:bg-inset data-[selected]:text-brand-soft-text",
    modal:
      "z-modal overflow-hidden rounded-12 border border-border-subtle bg-sheet shadow-lg outline-none",
    modalBackdrop: "fixed inset-0 z-modal-backdrop bg-overlay",
    title: "px-3 py-2 text-13 font-semibold text-fg",
    description: "px-3 pb-2 text-xs text-fg-muted",
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

export const POPUP_BASE = popoverVariants().content();
export const POPUP_INPUT = popoverVariants().input();
export const POPUP_ITEM = popoverVariants().item();
export const POPUP_LIST = popoverVariants().list();
export const MODAL_BASE = popoverVariants().modal();
export const MODAL_BACKDROP = popoverVariants().modalBackdrop();

type PopoverRecipeProps = VariantProps<typeof popoverVariants>;

export type PopoverSurface = NonNullable<PopoverRecipeProps["surface"]>;
export type PopoverRootProps<Payload = unknown> =
  BasePopoverRootProps<Payload>;
export type PopoverTriggerProps<Payload = unknown> =
  BasePopoverTriggerProps<Payload>;
export type PopoverPortalProps = BasePopoverPortalProps;
export type PopoverPositionerProps = BasePopoverPositionerProps;
export type PopoverArrowProps = BasePopoverArrowProps;
export type PopoverBackdropProps = BasePopoverBackdropProps;
export type PopoverCloseProps = BasePopoverCloseProps;
export type PopoverViewportProps = React.ComponentPropsWithoutRef<
  typeof BasePopover.Viewport
>;

export const PopoverRoot = BasePopover.Root;
export const PopoverPortal = BasePopover.Portal;
export const PopoverPositioner = BasePopover.Positioner;
export const PopoverArrow = BasePopover.Arrow;
export const PopoverBackdrop = BasePopover.Backdrop;
export const PopoverClose = BasePopover.Close;
export const PopoverViewport = BasePopover.Viewport;

// Thin re-export. Base UI's controlled `open`/`onOpenChange` owns the open/close
// transition; we no longer intercept the press here. The old rc.0-era capture-phase
// defer (swallow the trusted press, re-dispatch via `setTimeout`) is gone — heavy
// view-state work is de-prioritized at its owner (a React transition in the resource-view
// store), not by re-timing the trigger.
export const PopoverTrigger = BasePopover.Trigger;

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

export function usePopoverVirtualAnchor(
  rect: PopoverVirtualAnchorRect,
): VirtualElement {
  const { x, y, width = 0, height = 0, contextElement = null } = rect;
  return React.useMemo(
    () => createPopoverVirtualAnchor({ x, y, width, height, contextElement }),
    [contextElement, height, width, x, y],
  );
}

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

export const PopoverPopup = PopoverContent;

export type PopoverTitleProps = BasePopoverTitleProps & {
  className?: string;
};

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

export const PopoverList = React.forwardRef<HTMLDivElement, PopoverListProps>(
  function PopoverList({ className, ...props }, ref) {
    const styles = popoverVariants();
    return <div ref={ref} className={styles.list({ className })} {...props} />;
  },
);
PopoverList.displayName = "PopoverList";

export type PopoverInputProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "className" | "color"
> & {
  className?: string;
};

export const PopoverInput = React.forwardRef<
  HTMLInputElement,
  PopoverInputProps
>(function PopoverInput({ className, ...props }, ref) {
  const styles = popoverVariants();
  return <input ref={ref} className={styles.input({ className })} {...props} />;
});
PopoverInput.displayName = "PopoverInput";

export type PopoverItemProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

export const PopoverItem = React.forwardRef<HTMLDivElement, PopoverItemProps>(
  function PopoverItem({ className, ...props }, ref) {
    const styles = popoverVariants();
    return <div ref={ref} className={styles.item({ className })} {...props} />;
  },
);
PopoverItem.displayName = "PopoverItem";

export const Popover = {
  Root: PopoverRoot,
  Trigger: PopoverTrigger,
  Portal: PopoverPortal,
  Positioner: PopoverPositioner,
  VirtualAnchor: PopoverVirtualAnchor,
  Content: PopoverContent,
  Popup: PopoverPopup,
  List: PopoverList,
  Input: PopoverInput,
  Item: PopoverItem,
  Arrow: PopoverArrow,
  Backdrop: PopoverBackdrop,
  Title: PopoverTitle,
  Description: PopoverDescription,
  Close: PopoverClose,
  Viewport: PopoverViewport,
};
