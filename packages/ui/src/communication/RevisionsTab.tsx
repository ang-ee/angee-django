import * as React from "react";
import { useResourceRevisions } from "../data/revisions";
import { errorMessage } from "../feedback/error-message";
import { revisionSnapshot } from "@angee/refine";

import { useUiT } from "../i18n";
import { EmptyState } from "../fragments/EmptyState";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
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
        description={errorMessage(revisions.error, t("revisions.unavailable"))}
      />
    );
  }
  if (revisions.fetching && revisions.revisions.length === 0) {
    return <SkeletonStatus label={t("revisions.loading")} className="flex flex-col gap-3 p-4">
      {[0, 1, 2].map((index) => <div key={index} className="flex gap-3">
        <Skeleton className="h-8 w-8 rounded-full" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-1/2" /><Skeleton className="h-3 w-1/3" />
        </div>
      </div>)}
    </SkeletonStatus>;
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
