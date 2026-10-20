import * as React from "react";
import { useInvalidate } from "@refinedev/core";
import { useResourceInvalidates, type Row } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  NavLink,
  Skeleton,
  StatusBadge,
  useEnumOptions,
  useResourceRecordHrefLookup,
  useResourceViewMaybe,
  type ListColumn,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

import { RecordRunStatusDocument, RUN_MODEL, RUN_MODELS } from "./documents.console";
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
  const t = useWorkflowsT();
  const statusOptions = useEnumOptions(RUN_MODEL, "status");
  const recordHref = useResourceRecordHrefLookup();
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
  // The row projects facts its run produces (an import's resulting document, an archive
  // import's results): when the run moves, the row the list holds is stale, so the live
  // run change refetches the row's resource the same way a verb that mutates it would,
  // through the metadata owner's resource invalidations. The first settled value is the
  // row's current state, not a transition.
  const invalidate = useInvalidate();
  const invalidations = useResourceInvalidates(resource ? [resource] : undefined);
  const transition = query.data ? (run ? `${run.id}:${run.status}` : "none") : undefined;
  const seen = React.useRef<string | undefined>(undefined);
  React.useEffect(() => {
    if (transition === undefined) return;
    if (seen.current === undefined) {
      seen.current = transition;
      return;
    }
    if (transition === seen.current) return;
    seen.current = transition;
    for (const params of invalidations) void invalidate(params);
  }, [invalidate, invalidations, transition]);
  if (!records.length) return null;
  if (query.isLoading) return <Skeleton shape="text" size="sm" className="w-16" />;
  if (!run) return <span aria-label={query.error ? t("run.statusUnavailable") : undefined}>—</span>;
  // The status is the run's: the pill opens the run, so a reader can follow what it is waiting on.
  const href = recordHref(RUN_MODEL, run.id);
  const badge = <StatusBadge value={run.status} options={statusOptions} />;
  return href ? <NavLink href={href} variant="inline" className="no-underline">{badge}</NavLink> : badge;
}

export const runStatusWidget = {
  read: RunStatusCell,
  cell: RunStatusCell,
} satisfies WidgetDefinition<unknown, Row>;
