import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { Alert, alertVariants, type BannerProps } from "../ui/alert";

export type ErrorBannerProps = Omit<
  BannerProps,
  "children" | "tone" | "format" | "title"
> & {
  message: React.ReactNode | null;
  title?: React.ReactNode;
};

export const ErrorBanner = React.forwardRef<HTMLDivElement, ErrorBannerProps>(
  function ErrorBanner(
    {
      actions,
      className,
      dismissLabel = "Dismiss",
      message,
      onDismiss,
      title,
      ...props
    },
    ref,
  ) {
    if (!message) return null;
    const styles = alertVariants({ format: "banner" });
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
        ref={ref}
        actions={
          actions || dismissAction ? (
            <>
              {actions}
              {dismissAction}
            </>
          ) : undefined
        }
        className={className}
        tone="danger"
        format="banner"
        title={title}
        {...props}
      >
        <span className="block truncate">{message}</span>
      </Alert>
    );
  },
);
ErrorBanner.displayName = "ErrorBanner";
