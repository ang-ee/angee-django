import { Alert, LoadingPanel } from "@angee/ui";
import type { ReactNode } from "react";

/**
 * The shared pane scaffold for every operator console section: a titled column
 * that gates its body on the daemon snapshot. A load failure (before any
 * snapshot) shows a contained danger `Alert` in place of the body — checked
 * ahead of loading so a continuously-failing daemon stays on the error rather
 * than flashing the spinner on each poll. While the first snapshot loads it
 * shows a section-supplied skeleton (or the framework `LoadingPanel` fallback);
 * once resolved it renders the children, with a transient `actionError`
 * surfaced as a danger `Alert` above them.
 */
export interface OperatorSectionProps {
  title?: ReactNode;
  /** True while the first snapshot is still loading (no data yet). */
  loading?: boolean;
  /** A load error shown in place of the body when no snapshot has arrived. */
  error?: Error | null;
  /** Message shown beside the loading spinner. */
  loadingMessage?: string;
  /** Representative loading body for this section's loaded view. */
  loadingContent?: ReactNode;
  /** A transient action error shown as a danger banner above the body. */
  actionError?: string | null;
  children: ReactNode;
}

export function OperatorSection({
  title,
  loading = false,
  error = null,
  loadingMessage,
  loadingContent,
  actionError = null,
  children,
}: OperatorSectionProps): ReactNode {
  return (
    <div className="flex flex-col gap-4">
      {title ? <h2 className="text-lg font-semibold text-fg">{title}</h2> : null}
      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading ? (
        loadingContent ? (
          <OperatorLoadingContent message={loadingMessage}>
            {loadingContent}
          </OperatorLoadingContent>
        ) : (
          <LoadingPanel message={loadingMessage} />
        )
      ) : (
        <>
          {actionError ? <Alert tone="danger">{actionError}</Alert> : null}
          {children}
        </>
      )}
    </div>
  );
}

function OperatorLoadingContent({
  message,
  children,
}: {
  message?: string;
  children: ReactNode;
}): ReactNode {
  return (
    <div aria-busy="true" aria-live="polite" role="status">
      {message ? <span className="sr-only">{message}</span> : null}
      {children}
    </div>
  );
}
