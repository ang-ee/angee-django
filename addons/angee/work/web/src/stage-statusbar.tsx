import {
  Skeleton,
  SkeletonStatus,
  StatusbarSteps,
  relationValueId,
  useRelationOptions,
  type WidgetRenderProps,
} from "@angee/ui";
import * as React from "react";

import { useWorkT } from "./i18n";
import { STAGE_MODEL } from "./resources";

const stageRelation = { resource: STAGE_MODEL, labelField: "name", canCreate: false };

type StageRow = {
  queue?: { id?: string } | string | null;
  stage?: { id?: string; name?: string } | string | null;
};

/**
 * A task's stage as a statusbar: the stages of the task's queue in pipeline
 * order, the current one emphasised. Read-only: stage moves go through the
 * queue's verbs (triage, rule-owned stages), never a free click. When the
 * actor cannot read the queue's stages, only the current stage is shown.
 */
export function StageStatusbar({ value, row }: WidgetRenderProps<unknown, StageRow>): React.ReactElement | null {
  const t = useWorkT();
  const queueId = relationValueId(row?.queue);
  const current = relationValueId(value) || relationValueId(row?.stage);
  const currentName = typeof row?.stage === "object" ? row?.stage?.name : undefined;
  const { options, list } = useRelationOptions(stageRelation, {
    enabled: Boolean(queueId),
    filters: queueId ? [{ field: "queue", operator: "eq", value: queueId }] : [],
    sorters: [{ field: "position", order: "asc" }],
  });
  if (!current) return null;
  if (list.fetching && options.length === 0) {
    return (
      <SkeletonStatus label={t("task.stage.label")} className="flex gap-1">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-24" />
      </SkeletonStatus>
    );
  }
  const steps = options.length > 0
    ? options
    : [{ value: current, label: currentName ?? current }];
  return <StatusbarSteps aria-label={t("task.stage.label")} steps={steps} value={current} readOnly />;
}
