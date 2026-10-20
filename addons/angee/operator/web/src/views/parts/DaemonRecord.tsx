import type { Row } from "@angee/metadata";
import {
  CommandBand, EmptyState, LoadingPanel, RecordActionBar, RecordHeader, useBreadcrumbLeafLabel,
  type EmptyStateProps,
} from "@angee/ui";
import type { ReactElement, ReactNode } from "react";

import { verbDescriptors, type DaemonVerb } from "./daemon-verbs";

export interface DaemonRecordProps<TSubject extends object> {
  /** The daemon record, or null while the snapshot loads or when the name matches nothing. */
  record: TSubject | null;
  /** True while the first snapshot is still loading. */
  loading: boolean;
  loadingMessage: string;
  /** The empty state when the route names a record the daemon does not have. */
  notFound: Pick<EmptyStateProps, "icon" | "title" | "description">;
  /** The record's kind, as the header eyebrow. */
  eyebrow: ReactNode;
  title: string | undefined;
  meta?: ReactNode;
  verbs: readonly DaemonVerb<TSubject>[];
  children: ReactNode;
}

/**
 * A daemon record page: the command band (the record's verbs through
 * `RecordActionBar`), the record header (kind · name · facts) and the body.
 * Daemon records are not model rows, so the band carries no `form#chrome`
 * controls and no pager.
 */
export function DaemonRecord<TSubject extends object>({
  record, loading, loadingMessage, notFound, eyebrow, title, meta, verbs, children,
}: DaemonRecordProps<TSubject>): ReactElement {
  useBreadcrumbLeafLabel(title ?? null, true);
  if (loading) return <LoadingPanel message={loadingMessage} />;
  if (!record) return <EmptyState fill {...notFound} />;
  return (
    <>
      <CommandBand verbs={<RecordActionBar record={record as Row} actions={verbDescriptors(verbs)} menuPlacement="leading" />} />
      <div className="flex min-h-0 flex-1 flex-col bg-canvas">
        <RecordHeader density="compact" crumbs={eyebrow} title={title} meta={meta} />
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-3">{children}</div>
      </div>
    </>
  );
}
