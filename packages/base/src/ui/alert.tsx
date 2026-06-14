import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { INTENT_GLYPHS } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

export const alertVariants = tv({
  slots: {
    root: "flex gap-3 border text-13",
    icon: "mt-0.5 flex size-4 shrink-0 items-center justify-center [&_.glyph]:size-4",
    body: "min-w-0 flex-1",
    title: "font-medium leading-snug",
    description: "leading-snug",
    actions: "ml-auto flex shrink-0 items-center gap-2",
    dismiss:
      "inline-flex size-icon-btn-sm items-center justify-center rounded text-current outline-none hover:bg-inset/50 focus-visible:focus-ring [&_.glyph]:size-3.5",
  },
  variants: {
    intent: {
      info: {
        root: "border-info-soft bg-info-soft text-info-text",
        icon: "text-info-text",
        title: "text-info-text",
        description: "text-info-text",
      },
      success: {
        root: "border-success-soft bg-success-soft text-success-text",
        icon: "text-success-text",
        title: "text-success-text",
        description: "text-success-text",
      },
      warning: {
        root: "border-warning-soft bg-warning-soft text-warning-text",
        icon: "text-warning-text",
        title: "text-warning-text",
        description: "text-warning-text",
      },
      danger: {
        root: "border-danger-soft bg-danger-soft text-danger-text",
        icon: "text-danger-text",
        title: "text-danger-text",
        description: "text-danger-text",
      },
    },
    surface: {
      alert: { root: "rounded-md px-4 py-3" },
      banner: { root: "rounded-none border-x-0 border-t-0 px-6 py-2" },
    },
  },
  defaultVariants: {
    intent: "info",
    surface: "alert",
  },
});

type AlertRecipeProps = VariantProps<typeof alertVariants>;

export type AlertIntent = NonNullable<AlertRecipeProps["intent"]>;
export type AlertSurface = NonNullable<AlertRecipeProps["surface"]>;

export type AlertProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color" | "title"
> &
  AlertRecipeProps & {
    actions?: React.ReactNode;
    className?: string;
    icon?: React.ReactNode | false;
    title?: React.ReactNode;
  };

export const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  function Alert(
    {
      actions,
      children,
      className,
      icon,
      intent = "info",
      role,
      surface = "alert",
      title,
      ...props
    },
    ref,
  ) {
    const styles = alertVariants({ intent, surface });
    const resolvedIcon =
      icon === false ? null : (icon ?? <Glyph decorative name={INTENT_GLYPHS[intent]} />);

    return (
      <div
        ref={ref}
        role={role ?? (intent === "danger" ? "alert" : "status")}
        className={styles.root({ className })}
        {...props}
      >
        {resolvedIcon ? <span className={styles.icon()}>{resolvedIcon}</span> : null}
        <div className={styles.body()}>
          {title ? <div className={styles.title()}>{title}</div> : null}
          {children ? <div className={styles.description()}>{children}</div> : null}
        </div>
        {actions ? <div className={styles.actions()}>{actions}</div> : null}
      </div>
    );
  },
);
Alert.displayName = "Alert";

export type AlertTitleProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> &
  Pick<AlertRecipeProps, "intent"> & {
    className?: string;
  };

export const AlertTitle = React.forwardRef<HTMLDivElement, AlertTitleProps>(
  function AlertTitle({ className, intent = "info", ...props }, ref) {
    const styles = alertVariants({ intent });
    return <div ref={ref} className={styles.title({ className })} {...props} />;
  },
);
AlertTitle.displayName = "AlertTitle";

export type AlertDescriptionProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> &
  Pick<AlertRecipeProps, "intent"> & {
    className?: string;
  };

export const AlertDescription = React.forwardRef<
  HTMLDivElement,
  AlertDescriptionProps
>(function AlertDescription({ className, intent = "info", ...props }, ref) {
  const styles = alertVariants({ intent });
  return (
    <div ref={ref} className={styles.description({ className })} {...props} />
  );
});
AlertDescription.displayName = "AlertDescription";

export type BannerProps = AlertProps & {
  dismissLabel?: string;
  onDismiss?: () => void;
};

export function Banner({
  actions,
  dismissLabel = "Dismiss",
  onDismiss,
  surface = "banner",
  ...props
}: BannerProps): React.ReactElement {
  const styles = alertVariants({
    intent: props.intent ?? "info",
    surface,
  });
  const dismissAction = onDismiss ? (
    <button
      type="button"
      aria-label={dismissLabel}
      className={styles.dismiss()}
      onClick={onDismiss}
    >
      <Glyph decorative name="x" />
    </button>
  ) : null;

  return (
    <Alert
      actions={
        actions || dismissAction ? (
          <>
            {actions}
            {dismissAction}
          </>
        ) : undefined
      }
      surface={surface}
      {...props}
    />
  );
}
