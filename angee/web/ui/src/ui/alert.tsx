import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { INTENT_GLYPHS, toneClass, type FeedbackIntent, type Fill } from "../lib/tones";
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
      "inline-flex size-icon-btn-sm items-center justify-center rounded-6 text-current outline-none hover:bg-inset/50 focus-visible:focus-ring [&_.glyph]:size-3.5",
  },
  variants: {
    // `format` is the layout axis (full card vs edge-to-edge banner); the color
    // fill is the orthogonal `variant` applied via the tone matrix.
    format: {
      alert: { root: "rounded-6 px-4 py-3" },
      banner: { root: "rounded-none border-x-0 border-t-0 px-6 py-2" },
    },
  },
  defaultVariants: {
    format: "alert",
  },
});

type AlertRecipeProps = VariantProps<typeof alertVariants>;

/** Alerts speak in feedback tones (a subset of the palette) so they carry a glyph. */
export type AlertTone = FeedbackIntent;
export type AlertFormat = NonNullable<AlertRecipeProps["format"]>;

export type AlertProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color" | "title"
> & {
    actions?: React.ReactNode;
    className?: string;
    format?: AlertFormat;
    icon?: React.ReactNode | false;
    title?: React.ReactNode;
    tone?: AlertTone;
    variant?: Fill;
  };

export const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  function Alert(
    {
      actions,
      children,
      className,
      format = "alert",
      icon,
      role,
      title,
      tone = "info",
      variant = "soft",
      ...props
    },
    ref,
  ) {
    const styles = alertVariants({ format });
    const resolvedIcon =
      icon === false ? null : (icon ?? <Glyph decorative name={INTENT_GLYPHS[tone]} />);

    return (
      <div
        ref={ref}
        role={role ?? (tone === "danger" ? "alert" : "status")}
        className={cn(styles.root(), toneClass(tone, variant), className)}
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
> & {
    className?: string;
  };

export const AlertTitle = React.forwardRef<HTMLDivElement, AlertTitleProps>(
  function AlertTitle({ className, ...props }, ref) {
    const styles = alertVariants();
    return <div ref={ref} className={styles.title({ className })} {...props} />;
  },
);
AlertTitle.displayName = "AlertTitle";

export type AlertDescriptionProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> & {
    className?: string;
  };

export const AlertDescription = React.forwardRef<
  HTMLDivElement,
  AlertDescriptionProps
>(function AlertDescription({ className, ...props }, ref) {
  const styles = alertVariants();
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
  dismissLabel,
  onDismiss,
  format = "banner",
  ...props
}: BannerProps): React.ReactElement {
  const t = useBaseT();
  const styles = alertVariants({ format });
  const dismissAction = onDismiss ? (
    <button
      type="button"
      aria-label={dismissLabel ?? t("alert.dismiss")}
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
      format={format}
      {...props}
    />
  );
}
