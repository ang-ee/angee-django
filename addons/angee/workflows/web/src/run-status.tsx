import type { Row } from "@angee/metadata";
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
  const records = resource && id ? [{ model: resource, id }] : [];
  const query = useAuthoredQuery(RecordRunStatusDocument, { records }, {
    models: RUN_MODELS,
    records,
    relatedModels: RUN_MODELS,
    enabled: records.length === 1,
  });
  if (!records.length) return null;
  if (query.isLoading) return <Skeleton shape="text" size="sm" className="w-16" />;
  const run = query.data?.record_timeline.records[0]?.runs[0];
  if (!run) return <span aria-label={query.error ? "Run status unavailable" : undefined}>—</span>;
  const Status = defaultWidgets.statusBadge.read;
  return <Status value={run.status} />;
}

export const runStatusWidget = {
  read: RunStatusCell,
  cell: RunStatusCell,
} satisfies WidgetDefinition<unknown, Row>;
