import * as React from "react";
import {
  useResourceRevisions,
  type ResourceRevision,
  type UseResourceRevisionsResult,
} from "@angee/sdk";

import { EmptyState } from "../fragments/EmptyState";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { TimelineEntry } from "../fragments/TimelineEntry";

export interface RevisionsTabProps {
  model: string;
  recordId: string | null | undefined;
  enabled?: boolean;
  result?: UseResourceRevisionsResult;
}

const REVISION_META_FIELDS = new Set(["id", "createdAt", "comment", "__typename"]);

export function RevisionsTab({
  enabled = true,
  model,
  recordId,
  result,
}: RevisionsTabProps): React.ReactElement {
  const activeRecordId = typeof recordId === "string" && recordId !== ""
    ? recordId
    : null;
  const owned = useResourceRevisions(model, activeRecordId, {
    enabled: result === undefined && enabled && activeRecordId !== null,
  });
  const revisions = result ?? owned;

  if (!activeRecordId) {
    return (
      <EmptyState
        icon="activity"
        title="No record selected"
        description="Open a record to view revisions."
        className="min-h-48 p-4"
      />
    );
  }
  if (revisions.error) {
    return (
      <ErrorBanner
        title="Revisions unavailable"
        message={revisions.error.message}
      />
    );
  }
  if (revisions.fetching && revisions.revisions.length === 0) {
    return <LoadingPanel message="Loading revisions" />;
  }
  if (revisions.revisions.length === 0) {
    return (
      <EmptyState
        icon="activity"
        title="No revisions yet"
        description="Field changes will appear here."
        className="min-h-48 p-4"
      />
    );
  }

  return (
    <ol className="flex flex-col gap-3">
      {revisions.revisions.map((revision) => (
        <TimelineEntry
          key={revision.id}
          title={revision.comment ?? "Record updated"}
          timestamp={revision.createdAt}
          body={revisionSnapshot(revision)}
        />
      ))}
    </ol>
  );
}

function revisionSnapshot(revision: ResourceRevision): unknown {
  if (typeof revision.body === "string") return revision.body;
  for (const [field, value] of Object.entries(revision)) {
    if (!REVISION_META_FIELDS.has(field) && value != null) return value;
  }
  return "";
}
