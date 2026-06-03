import { Spinner } from "@angee/base";
import type { ReactNode } from "react";

/** Shared loading / error / empty states for the operator section panes. */

export function SectionLoading({ label }: { label: string }): ReactNode {
  return (
    <div
      aria-live="polite"
      className="flex min-h-48 items-center justify-center gap-3 text-sm text-fg-muted"
      role="status"
    >
      <Spinner size="md" tone="brand" />
      <span>{label}</span>
    </div>
  );
}

export function SectionError({ message }: { message: string }): ReactNode {
  return (
    <div
      className="rounded-md border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger"
      role="alert"
    >
      {message}
    </div>
  );
}

export function SectionEmpty({ message }: { message: string }): ReactNode {
  return (
    <div className="rounded-md border border-border bg-surface px-4 py-6 text-center text-13 text-fg-muted">
      {message}
    </div>
  );
}
