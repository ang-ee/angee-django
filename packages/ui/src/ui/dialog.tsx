import * as React from "react";
import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import type {
  DialogBackdropProps as BaseDialogBackdropProps,
  DialogCloseProps as BaseDialogCloseProps,
  DialogDescriptionProps as BaseDialogDescriptionProps,
  DialogPortalProps as BaseDialogPortalProps,
  DialogPopupProps as BaseDialogPopupProps,
  DialogRootProps as BaseDialogRootProps,
  DialogTitleProps as BaseDialogTitleProps,
  DialogTriggerProps as BaseDialogTriggerProps,
} from "@base-ui/react/dialog";
import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { tv, type VariantProps } from "../lib/variants";

export const dialogVariants = tv({
  slots: {
    backdrop:
      "fixed inset-0 z-modal-backdrop bg-overlay animate-apps-modal-fade",
    content:
      "fixed left-1/2 z-modal max-w-[calc(100vw-2rem)] -translate-x-1/2 overflow-hidden rounded-12 border border-border-subtle bg-sheet shadow-lg outline-none animate-apps-modal-pop",
    header: "space-y-1.5 px-5 pt-5",
    body: "px-5 py-3 text-13 text-fg-2",
    footer:
      "flex items-center justify-end gap-2 border-t border-border-subtle bg-sheet-2 px-5 py-3",
    title: "text-base font-semibold leading-snug text-fg",
    description: "text-13 leading-relaxed text-fg-2",
    close:
      "inline-flex size-icon-btn-sm items-center justify-center rounded text-fg-muted outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring [&_svg]:size-3.5",
  },
  variants: {
    size: {
      sm: { content: "w-[28rem]" },
      md: { content: "w-modal-w" },
      lg: { content: "w-[44rem]" },
    },
    placement: {
      default: { content: "top-modal-top" },
      prompt: { content: "top-[30vh]" },
      center: { content: "top-1/2 -translate-y-1/2" },
    },
  },
  defaultVariants: {
    size: "md",
    placement: "default",
  },
});

type DialogRecipeProps = VariantProps<typeof dialogVariants>;

export type DialogSize = NonNullable<DialogRecipeProps["size"]>;
export type DialogPlacement = NonNullable<DialogRecipeProps["placement"]>;
export type DialogRootProps<Payload = unknown> = BaseDialogRootProps<Payload>;
export type DialogTriggerProps<Payload = unknown> =
  BaseDialogTriggerProps<Payload>;
export type DialogPortalProps = BaseDialogPortalProps;

export const DialogRoot = BaseDialog.Root;
export const DialogTrigger = BaseDialog.Trigger;
export const DialogPortal = BaseDialog.Portal;

export type DialogBackdropProps = BaseDialogBackdropProps & {
  className?: string;
};

export const DialogBackdrop = React.forwardRef<
  HTMLDivElement,
  DialogBackdropProps
>(function DialogBackdrop({ className, ...props }, ref) {
  const styles = dialogVariants();
  return (
    <BaseDialog.Backdrop
      ref={ref}
      className={styles.backdrop({ className })}
      {...props}
    />
  );
});
DialogBackdrop.displayName = "DialogBackdrop";

export type DialogContentProps = BaseDialogPopupProps &
  Pick<DialogRecipeProps, "placement" | "size"> & {
    className?: string;
  };

export const DialogContent = React.forwardRef<
  HTMLDivElement,
  DialogContentProps
>(function DialogContent(
  { className, placement = "default", size = "md", ...props },
  ref,
) {
  const styles = dialogVariants({ placement, size });
  return (
    <BaseDialog.Popup
      ref={ref}
      className={styles.content({ className })}
      {...props}
    />
  );
});
DialogContent.displayName = "DialogContent";

export type DialogHeaderProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

export const DialogHeader = React.forwardRef<
  HTMLDivElement,
  DialogHeaderProps
>(function DialogHeader({ className, ...props }, ref) {
  const styles = dialogVariants();
  return <div ref={ref} className={styles.header({ className })} {...props} />;
});
DialogHeader.displayName = "DialogHeader";

export type DialogBodyProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

export const DialogBody = React.forwardRef<HTMLDivElement, DialogBodyProps>(
  function DialogBody({ className, ...props }, ref) {
    const styles = dialogVariants();
    return <div ref={ref} className={styles.body({ className })} {...props} />;
  },
);
DialogBody.displayName = "DialogBody";

export type DialogFooterProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

export const DialogFooter = React.forwardRef<
  HTMLDivElement,
  DialogFooterProps
>(function DialogFooter({ className, ...props }, ref) {
  const styles = dialogVariants();
  return <div ref={ref} className={styles.footer({ className })} {...props} />;
});
DialogFooter.displayName = "DialogFooter";

export type DialogTitleProps = BaseDialogTitleProps & {
  className?: string;
};

export const DialogTitle = React.forwardRef<
  HTMLParagraphElement,
  DialogTitleProps
>(function DialogTitle({ className, ...props }, ref) {
  const styles = dialogVariants();
  return (
    <BaseDialog.Title
      ref={ref}
      className={styles.title({ className })}
      {...props}
    />
  );
});
DialogTitle.displayName = "DialogTitle";

export type DialogDescriptionProps = BaseDialogDescriptionProps & {
  className?: string;
};

export const DialogDescription = React.forwardRef<
  HTMLParagraphElement,
  DialogDescriptionProps
>(function DialogDescription({ className, ...props }, ref) {
  const styles = dialogVariants();
  return (
    <BaseDialog.Description
      ref={ref}
      className={styles.description({ className })}
      {...props}
    />
  );
});
DialogDescription.displayName = "DialogDescription";

export type DialogCloseProps = BaseDialogCloseProps & {
  className?: string;
};

export const DialogClose = React.forwardRef<
  HTMLButtonElement,
  DialogCloseProps
>(function DialogClose({ className, children, ...props }, ref) {
  const t = useBaseT();
  const styles = dialogVariants();
  const ariaLabel = props["aria-label"] ?? (children ? undefined : t("dialog.close"));
  return (
    <BaseDialog.Close
      ref={ref}
      aria-label={ariaLabel}
      className={styles.close({ className })}
      {...props}
    >
      {children ?? <Glyph name="x" strokeWidth={2.25} />}
    </BaseDialog.Close>
  );
});
DialogClose.displayName = "DialogClose";

export const Dialog = {
  Root: DialogRoot,
  Trigger: DialogTrigger,
  Portal: DialogPortal,
  Backdrop: DialogBackdrop,
  Content: DialogContent,
  Header: DialogHeader,
  Body: DialogBody,
  Footer: DialogFooter,
  Title: DialogTitle,
  Description: DialogDescription,
  Close: DialogClose,
};
