import * as React from "react";
import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import type {
  DialogPopupProps as BaseDialogPopupProps,
} from "@base-ui/react/dialog";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { textRoleVariants } from "./text";
import {
  DialogBackdrop,
  DialogClose,
  DialogDescription,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  DialogTrigger,
  type DialogBackdropProps,
  type DialogBodyProps,
  type DialogCloseProps,
  type DialogDescriptionProps,
  type DialogFooterProps,
  type DialogHeaderProps,
  type DialogPortalProps,
  type DialogRootProps,
  type DialogTitleProps,
  type DialogTriggerProps,
} from "./dialog";

export const drawerVariants = tv({
  slots: {
    content:
      "fixed z-modal flex flex-col overflow-hidden border-border-subtle bg-sheet shadow-lg outline-none transition-transform duration-200 ease-out",
    header: "space-y-1.5 border-b border-border-subtle px-5 py-4",
    body: cn(textRoleVariants({ role: "description" }), "min-h-0 flex-1 overflow-y-auto px-5 py-4"),
    footer:
      "flex items-center justify-end gap-2 border-t border-border-subtle bg-sheet-2 px-5 py-3",
  },
  variants: {
    side: {
      right: {
        content:
          "right-0 top-0 h-dvh w-[min(32rem,calc(100vw-2rem))] border-l data-[ending-style]:translate-x-full data-[starting-style]:translate-x-full",
      },
      left: {
        content:
          "left-0 top-0 h-dvh w-[min(32rem,calc(100vw-2rem))] border-r data-[ending-style]:-translate-x-full data-[starting-style]:-translate-x-full",
      },
      top: {
        content:
          "inset-x-0 top-0 max-h-[85dvh] border-b data-[ending-style]:-translate-y-full data-[starting-style]:-translate-y-full",
      },
      bottom: {
        content:
          "inset-x-0 bottom-0 max-h-[85dvh] border-t data-[ending-style]:translate-y-full data-[starting-style]:translate-y-full",
      },
    },
  },
  defaultVariants: {
    side: "right",
  },
});

type DrawerRecipeProps = VariantProps<typeof drawerVariants>;

export type DrawerSide = NonNullable<DrawerRecipeProps["side"]>;
export type DrawerRootProps<Payload = unknown> = DialogRootProps<Payload>;
export type DrawerTriggerProps<Payload = unknown> = DialogTriggerProps<Payload>;
export type DrawerPortalProps = DialogPortalProps;
export type DrawerBackdropProps = DialogBackdropProps;
export type DrawerHeaderProps = DialogHeaderProps;
export type DrawerBodyProps = DialogBodyProps;
export type DrawerFooterProps = DialogFooterProps;
export type DrawerTitleProps = DialogTitleProps;
export type DrawerDescriptionProps = DialogDescriptionProps;
export type DrawerCloseProps = DialogCloseProps;

export const DrawerRoot = DialogRoot;
export const DrawerTrigger = DialogTrigger;
export const DrawerPortal = DialogPortal;
export const DrawerBackdrop = DialogBackdrop;
export const DrawerTitle = DialogTitle;
export const DrawerDescription = DialogDescription;
export const DrawerClose = DialogClose;

export type DrawerContentProps = BaseDialogPopupProps &
  Pick<DrawerRecipeProps, "side"> & {
    className?: string;
  };

export const DrawerContent = React.forwardRef<
  HTMLDivElement,
  DrawerContentProps
>(function DrawerContent({ className, side = "right", ...props }, ref) {
  const styles = drawerVariants({ side });
  return (
    <BaseDialog.Popup
      ref={ref}
      className={styles.content({ className })}
      data-side={side}
      {...props}
    />
  );
});
DrawerContent.displayName = "DrawerContent";

export const DrawerHeader = React.forwardRef<
  HTMLDivElement,
  DrawerHeaderProps
>(function DrawerHeader({ className, ...props }, ref) {
  const styles = drawerVariants();
  return <div ref={ref} className={styles.header({ className })} {...props} />;
});
DrawerHeader.displayName = "DrawerHeader";

export const DrawerBody = React.forwardRef<HTMLDivElement, DrawerBodyProps>(
  function DrawerBody({ className, ...props }, ref) {
    const styles = drawerVariants();
    return <div ref={ref} className={styles.body({ className })} {...props} />;
  },
);
DrawerBody.displayName = "DrawerBody";

export const DrawerFooter = React.forwardRef<
  HTMLDivElement,
  DrawerFooterProps
>(function DrawerFooter({ className, ...props }, ref) {
  const styles = drawerVariants();
  return <div ref={ref} className={styles.footer({ className })} {...props} />;
});
DrawerFooter.displayName = "DrawerFooter";

export const Drawer = Object.assign(DrawerRoot, {
  Root: DrawerRoot,
  Trigger: DrawerTrigger,
  Portal: DrawerPortal,
  Backdrop: DrawerBackdrop,
  Content: DrawerContent,
  Header: DrawerHeader,
  Body: DrawerBody,
  Footer: DrawerFooter,
  Title: DrawerTitle,
  Description: DrawerDescription,
  Close: DrawerClose,
});

export const Sheet = Drawer;
export const SheetContent = DrawerContent;
export const SheetHeader = DrawerHeader;
export const SheetBody = DrawerBody;
export const SheetFooter = DrawerFooter;
