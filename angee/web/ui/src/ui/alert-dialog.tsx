import * as React from "react";
import { AlertDialog as BaseAlertDialog } from "@base-ui/react/alert-dialog";
import type {
  AlertDialogRootProps as BaseAlertDialogRootProps,
} from "@base-ui/react/alert-dialog";

import { cn } from "../lib/cn";
import { toneClass } from "../lib/tones";
import { tv } from "../lib/variants";
import {
  Button,
  buttonVariants,
  type ButtonSize,
  type ButtonVariant,
} from "./button";
import {
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPortal,
  DialogTitle,
  type DialogBackdropProps,
  type DialogBodyProps,
  type DialogContentProps,
  type DialogDescriptionProps,
  type DialogFooterProps,
  type DialogHeaderProps,
  type DialogPortalProps,
  type DialogTitleProps,
} from "./dialog";

export const alertDialogVariants = tv({
  slots: {
    content: "",
    icon: "flex size-8 shrink-0 items-center justify-center rounded-full [&_svg]:size-4",
    cancel: "",
    action: "",
  },
});

/** Confirm dialogs speak in three feedback tones; the icon takes its soft fill. */
export type AlertDialogTone = "info" | "warning" | "danger";
export type AlertDialogRootProps<Payload = unknown> =
  BaseAlertDialogRootProps<Payload>;

export const AlertDialogRoot = BaseAlertDialog.Root;
export const AlertDialogTrigger = BaseAlertDialog.Trigger;
export const AlertDialogPortal = DialogPortal;
export const AlertDialogBackdrop = DialogBackdrop;
export const AlertDialogHeader = DialogHeader;
export const AlertDialogBody = DialogBody;
export const AlertDialogFooter = DialogFooter;
export const AlertDialogTitle = DialogTitle;
export const AlertDialogDescription = DialogDescription;

export type AlertDialogTriggerProps = React.ComponentPropsWithoutRef<
  typeof BaseAlertDialog.Trigger
>;
export type AlertDialogPortalProps = DialogPortalProps;
export type AlertDialogBackdropProps = DialogBackdropProps;
export type AlertDialogHeaderProps = DialogHeaderProps;
export type AlertDialogBodyProps = DialogBodyProps;
export type AlertDialogFooterProps = DialogFooterProps;
export type AlertDialogTitleProps = DialogTitleProps;
export type AlertDialogDescriptionProps = DialogDescriptionProps;

export type AlertDialogContentProps = DialogContentProps & {
    className?: string;
    tone?: AlertDialogTone;
  };

export const AlertDialogContent = React.forwardRef<
  HTMLDivElement,
  AlertDialogContentProps
>(function AlertDialogContent(
  { className, tone = "danger", placement = "prompt", size = "sm", ...props },
  ref,
) {
  const styles = alertDialogVariants();
  return (
    <DialogContent
      ref={ref}
      data-tone={tone}
      placement={placement}
      size={size}
      className={styles.content({ className })}
      {...props}
    />
  );
});
AlertDialogContent.displayName = "AlertDialogContent";

export type AlertDialogIconProps = React.HTMLAttributes<HTMLDivElement> & {
    className?: string;
    tone?: AlertDialogTone;
  };

export const AlertDialogIcon = React.forwardRef<
  HTMLDivElement,
  AlertDialogIconProps
>(function AlertDialogIcon({ className, tone = "danger", ...props }, ref) {
  const styles = alertDialogVariants();
  return (
    <div
      ref={ref}
      className={cn(styles.icon(), toneClass(tone, "soft"), className)}
      {...props}
    />
  );
});
AlertDialogIcon.displayName = "AlertDialogIcon";

export type AlertDialogCancelProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "className" | "color"
> & {
    className?: string;
    size?: ButtonSize;
    variant?: Exclude<ButtonVariant, "danger" | "primary">;
  };

export const AlertDialogCancel = React.forwardRef<
  HTMLButtonElement,
  AlertDialogCancelProps
>(function AlertDialogCancel(
  { className, size = "md", variant = "secondary", ...props },
  ref,
) {
  const styles = alertDialogVariants();
  return (
    <BaseAlertDialog.Close
      ref={ref}
      className={cn(
        buttonVariants({ size, variant }),
        styles.cancel({ className }),
      )}
      {...props}
    />
  );
});
AlertDialogCancel.displayName = "AlertDialogCancel";

export type AlertDialogActionProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "className" | "color"
> & {
    className?: string;
    loading?: boolean;
    size?: ButtonSize;
    tone?: AlertDialogTone;
    variant?: ButtonVariant;
  };

export const AlertDialogAction = React.forwardRef<
  HTMLElement,
  AlertDialogActionProps
>(function AlertDialogAction(
  { className, tone = "danger", variant, ...props },
  ref,
) {
  const styles = alertDialogVariants();
  const resolvedVariant =
    variant ?? (tone === "danger" ? "danger" : "primary");
  return (
    <Button
      ref={ref}
      className={styles.action({ className })}
      variant={resolvedVariant}
      {...props}
    />
  );
});
AlertDialogAction.displayName = "AlertDialogAction";

export const AlertDialog = {
  Root: AlertDialogRoot,
  Trigger: AlertDialogTrigger,
  Portal: AlertDialogPortal,
  Backdrop: AlertDialogBackdrop,
  Content: AlertDialogContent,
  Header: AlertDialogHeader,
  Body: AlertDialogBody,
  Footer: AlertDialogFooter,
  Title: AlertDialogTitle,
  Description: AlertDialogDescription,
  Icon: AlertDialogIcon,
  Cancel: AlertDialogCancel,
  Action: AlertDialogAction,
};
