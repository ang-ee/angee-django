import * as React from "react";
import { useResourceRevisions } from "../data/revisions";
import { revisionSnapshot } from "@angee/refine";

import { useUiT } from "../i18n";
import { EmptyState } from "../fragments/EmptyState";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { TimelineEntry } from "../fragments/TimelineEntry";

export interface RevisionsTabProps {
  resource: string;
  recordId: string | null | undefined;
  enabled?: boolean;
}

export function RevisionsTab({
  enabled = true,
  resource,
  recordId,
}: RevisionsTabProps): React.ReactElement {
  const t = useUiT();
  const activeRecordId = typeof recordId === "string" && recordId !== ""
    ? recordId
    : null;
  const revisions = useResourceRevisions(resource, activeRecordId, {
    enabled: enabled && activeRecordId !== null,
  });

  if (!activeRecordId) {
    return (
      <EmptyState
        icon="activity"
        title={t("revisions.noRecordTitle")}
        description={t("revisions.noRecordDescription")}
        className="min-h-48 p-4"
      />
    );
  }
  if (revisions.error) {
    return (
      <ErrorBanner
        title={t("revisions.unavailable")}
        description={revisions.error.message}
      />
    );
  }
  if (revisions.fetching && revisions.revisions.length === 0) {
    return <LoadingPanel message={t("revisions.loading")} />;
  }
  if (revisions.revisions.length === 0) {
    return (
      <EmptyState
        icon="activity"
        title={t("revisions.emptyTitle")}
        description={t("revisions.emptyDescription")}
        className="min-h-48 p-4"
      />
    );
  }

  return (
    <ol className="flex flex-col gap-3">
      {revisions.revisions.map((revision) => (
        <TimelineEntry
          key={revision.id}
          title={revision.comment ?? t("revisions.recordUpdated")}
          timestamp={revision.created_at}
          body={revisionSnapshot(revision)}
        />
      ))}
    </ol>
  );
}
