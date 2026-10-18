import * as React from "react";
import { useInvalidate } from "@refinedev/core";
import { refineResourceIdentifier, useModelMetadata, type Row } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  Skeleton,
  defaultWidgets,
  useResourceViewMaybe,
  type ListColumn,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { RecordRunStatusDocument, RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

/** A render-only column showing the latest workflow run about each resource row. */
export function runStatusColumn<TRow extends Row>(): ListColumn<TRow> {
  return {
    field: "workflow_run",
    header: <RunStatusLabel />,
    selectionPaths: ["id"],
    sortable: false,
    widget: "runStatus",
  };
}

function RunStatusLabel() {
  const t = useWorkflowsT();
  return <>{t("run.status")}</>;
}

function RunStatusCell({ row }: WidgetRenderProps<unknown, Row>) {
  const resource = useResourceViewMaybe()?.resource ?? "";
  const id = typeof row?.id === "string" ? row.id : "";
  const records = React.useMemo(() => (resource && id ? [{ model: resource, id }] : []), [resource, id]);
  const query = useAuthoredQuery(RecordRunStatusDocument, { records }, {
    models: RUN_MODELS,
    records,
    relatedModels: RUN_MODELS,
    enabled: records.length === 1,
  });
  const run = query.data?.record_timeline.records[0]?.runs[0];
  // The row projects facts its run produces (a bill import's imported bill, an archive
  // import's results): when the run moves, the row the list holds is stale, so the live
  // run change refetches the list and detail the same way a row mutation would.
  const invalidate = useInvalidate();
  const listResource = useModelMetadata(resource)?.resource ?? null;
  const transition = run ? `${run.id}:${run.status}` : "";
  const seen = React.useRef(transition);
  React.useEffect(() => {
    if (!listResource || transition === seen.current) return;
    seen.current = transition;
    // Lists key their queries on the resource identifier (`<schema>:<modelLabel>`).
    void invalidate({
      resource: refineResourceIdentifier(listResource),
      dataProviderName: listResource.schemaName,
      invalidates: ["list", "many", "detail"],
    });
  }, [invalidate, listResource, transition]);
  if (!records.length) return null;
  if (query.isLoading) return <Skeleton shape="text" size="sm" className="w-16" />;
  if (!run) return <span aria-label={query.error ? "Run status unavailable" : undefined}>—</span>;
  const Status = defaultWidgets.statusBadge.read;
  return <Status value={run.status} />;
}

export const runStatusWidget = {
  read: RunStatusCell,
  cell: RunStatusCell,
} satisfies WidgetDefinition<unknown, Row>;
