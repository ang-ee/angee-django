import {
  StatusbarSkeleton,
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
  stage?: { id?: string; name?: string; on_path?: boolean } | string | null;
  dropped_at?: string | null;
};

type StageOptionRow = { id?: unknown; on_path?: unknown };

/**
 * A task's stage as a statusbar: the stages of the task's queue in pipeline
 * order, the current one emphasised. Read-only: stage moves go through the
 * queue's verbs (triage, rule-owned stages), never a free click. When the
 * actor cannot read the queue's stages, only the current stage is shown.
 */
export function StageStatusbar({ value, row, field }: WidgetRenderProps<unknown, StageRow>): React.ReactElement | null {
  const t = useWorkT();
  const queueId = relationValueId(row?.queue);
  const current = relationValueId(value) || relationValueId(row?.stage);
  const currentName = typeof row?.stage === "object" ? row?.stage?.name : undefined;
  const { options, rows, list } = useRelationOptions(stageRelation, {
    enabled: Boolean(queueId),
    fields: ["on_path"],
    filters: queueId ? [{ field: "queue", operator: "eq", value: queueId }] : [],
    sorters: [{ field: "position", order: "asc" }],
  });
  if (!current) return null;
  if (list.fetching && options.length === 0) {
    return <StatusbarSkeleton count={3} />;
  }
  const pathById = new Map(rows.map((item) => {
    const stage = item as StageOptionRow;
    return [String(stage.id), stage.on_path !== false] as const;
  }));
  const steps = options.length > 0
    ? options.map((option) => ({ ...option, onPath: pathById.get(option.value) ?? true,
      date: option.value === current ? row?.dropped_at : undefined }))
    : [{ value: current, label: currentName ?? t("task.stage.label"),
      onPath: typeof row?.stage === "object" ? row.stage?.on_path !== false : true,
      date: row?.dropped_at }];
  return <StatusbarSteps aria-label={t("task.stage.label")} steps={steps} value={current} readOnly
    fill={field?.fill} containerWidth={field?.containerWidth} />;
}
