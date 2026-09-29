import { holdsPermission } from "@angee/metadata";
import { extractActionOutcome } from "@angee/refine";
import {
  Skeleton,
  SkeletonStatus,
  StatusbarSteps,
  relationValueId,
  useActionResultRun,
  useAuthoredResourceMutation,
  useConfirm,
  useRelationOptions,
  useRuntimeViewAs,
  type WidgetRenderProps,
} from "@angee/ui";
import * as React from "react";

import { SetProjectCurrentMilestoneDocument } from "./documents";
import { useProjectsT } from "./i18n";
import { MILESTONE_MODEL, PROJECT_MODEL, TASK_MODEL } from "./resources";

const milestoneRelation = { resource: MILESTONE_MODEL, labelField: "name", canCreate: false };

type PhaseRecord = {
  id?: string;
  revision?: number;
  permissions?: readonly string[];
  current_milestone?: { id: string; name: string } | null;
  selectable_milestones?: readonly { id: string }[];
};

/**
 * The project's phase statusbar: every milestone of the project in order, the
 * current one emphasised and earlier ones completed. A writer moves the phase
 * by choosing a selectable milestone; the form selection supplies every fact.
 */
export function ProjectPhaseControl({ value, row, readOnly = false }: WidgetRenderProps<unknown, PhaseRecord>): React.ReactElement | null {
  const t = useProjectsT();
  const preview = useRuntimeViewAs();
  const confirm = useConfirm();
  const settle = useActionResultRun();
  const recordId = row?.id;
  const canWrite = Boolean(recordId && !readOnly && !preview.viewAs && !preview.pending && holdsPermission(row, "write"));
  const selectable = new Set(canWrite ? row?.selectable_milestones?.map((item) => item.id) ?? [] : []);
  const { options, list } = useRelationOptions(milestoneRelation, {
    enabled: Boolean(recordId),
    filters: recordId ? [{ field: "project", operator: "eq", value: recordId }] : [],
    sorters: [{ field: "sort_order", order: "asc" }],
  });
  const [selectPhase, state] = useAuthoredResourceMutation(SetProjectCurrentMilestoneDocument, {
    invalidateModels: [PROJECT_MODEL, TASK_MODEL],
    shouldInvalidate: (data) => data?.set_project_current_milestone.ok === true,
  });
  if (!recordId) return null;
  const current = row?.current_milestone;
  if (list.fetching && options.length === 0) {
    return (
      <SkeletonStatus label={t("project.phase.select")} className="flex gap-1">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-24" />
      </SkeletonStatus>
    );
  }
  const steps = options.map((option) => ({ ...option, disabled: !selectable.has(option.value) }));
  return <StatusbarSteps
    aria-label={t("project.phase.select")}
    steps={steps}
    value={relationValueId(value) || current?.id}
    readOnly={!canWrite || state.fetching || selectable.size === 0}
    onChange={async (id) => {
      if (!canWrite || !selectable.has(id)) return;
      const selected = options.find((option) => option.value === id);
      if (!selected) return;
      if (!await confirm({
        title: t("project.phase.select"),
        body: t("project.phase.confirm", {
          previous: current?.name ?? t("project.phase.none"),
          selected: String(selected.label),
        }),
      })) return;
      await settle(async () => extractActionOutcome(await selectPhase({
        id: recordId,
        milestone: id,
        expected_revision: row?.revision,
      }), "set_project_current_milestone"));
    }}
  />;
}
