import * as React from "react";

import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { EmptyState } from "./EmptyState";

export interface ErrorPanelProps {
  /** The thrown value. Its message is shown when it reads as one. */
  error?: unknown;
  /** Retry handler. Omitted, the panel shows no action. */
  onRetry?: () => void;
  className?: string;
}

/**
 * The last-resort surface for a route that threw. It renders above the runtime
 * providers as well as under them, so every string falls back to bundled
 * English and the panel never throws on its way to reporting a throw.
 */
export function ErrorPanel({
  error,
  onRetry,
  className,
}: ErrorPanelProps): React.ReactElement {
  const t = useUiT();
  const detail =
    error instanceof Error && error.message ? error.message : null;
  return (
    <EmptyState
      fill
      className={className}
      icon="triangle-alert"
      title={t("appError.title")}
      description={detail ?? t("appError.description")}
      actions={
        onRetry ? (
          <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
            {t("appError.retry")}
          </Button>
        ) : undefined
      }
    />
  );
}
