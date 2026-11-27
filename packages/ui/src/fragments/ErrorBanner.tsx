import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { Alert, alertVariants, type BannerProps } from "../ui/alert";

export type ErrorBannerProps = Omit<
  BannerProps,
  "children" | "tone" | "format" | "title"
> & {
  /** The error body. `null` renders nothing — the "no error" state. Shares the
   *  `{title, description, actions}` vocabulary of the other state surfaces. */
  description?: React.ReactNode | null;
  /** The error body as separate messages, in place of `description`: one reads as the body, several
   *  as a list, each its own item to the eye and to a screen reader; none renders nothing. */
  messages?: readonly string[];
  title?: React.ReactNode;
};

/**
 * @kind fragment
 */
export const ErrorBanner = React.forwardRef<HTMLDivElement, ErrorBannerProps>(
  function ErrorBanner(
    {
      actions,
      className,
      description,
      messages,
      dismissLabel = "Dismiss",
      onDismiss,
      role,
      title,
      ...props
    },
    ref,
  ) {
    const body = messages === undefined ? description
      : messages.length > 1 ? <ul className="grid list-disc gap-1 ps-4">{messages.map((message, index) => <li key={index}>{message}</li>)}</ul>
      : messages[0];
    if (!body) return null;
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
        role={role ?? "alert"}
        title={title}
        {...props}
      >
        <div className="whitespace-pre-wrap [overflow-wrap:anywhere]">{body}</div>
      </Alert>
    );
  },
);
ErrorBanner.displayName = "ErrorBanner";
