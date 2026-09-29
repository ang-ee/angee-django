import { extractActionOutcome } from "@angee/refine";
import {
  RelationPicker,
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

interface PhaseRecord {
  id?: string;
  revision?: number;
  permissions?: readonly string[];
  current_milestone?: { id: string; name: string } | null;
  selectable_milestones?: readonly { id: string }[];
}

/** Saved project phase status widget; form selection supplies every project fact. */
export function ProjectPhaseControl({ value, row, readOnly = false }: WidgetRenderProps<unknown, PhaseRecord>): React.ReactElement | null {
  const t = useProjectsT();
  const preview = useRuntimeViewAs();
  const confirm = useConfirm();
  const settle = useActionResultRun();
  const recordId = row?.id;
  const canWrite = Boolean(recordId && !readOnly && !preview.viewAs && !preview.pending && row?.permissions?.includes("write"));
  const eligibleIds = canWrite ? row?.selectable_milestones?.map((item) => item.id) ?? [] : [];
  const { options, list } = useRelationOptions(milestoneRelation, {
    enabled: eligibleIds.length > 0,
    filters: [{ field: "id", operator: "in", value: eligibleIds }],
    sorters: [{ field: "sort_order", order: "asc" }],
  });
  const [selectPhase, state] = useAuthoredResourceMutation(SetProjectCurrentMilestoneDocument, {
    invalidateModels: [PROJECT_MODEL, TASK_MODEL],
    shouldInvalidate: (data) => data?.set_project_current_milestone.ok === true,
  });
  if (!recordId) return null;
  const current = row?.current_milestone;
  return <RelationPicker
    aria-label={t("project.phase.select")}
    value={relationValueId(value) || current?.id || ""}
    options={eligibleIds.length > 0 ? options : []}
    placeholder={current?.name ?? t("project.phase.none")}
    readOnly={!canWrite || state.fetching || eligibleIds.length === 0}
    searchState={{ pending: list.fetching, error: list.error, retry: list.refetch }}
    onChange={async (id) => {
      if (!canWrite) return;
      const selected = options.find((option) => option.value === id);
      if (!selected) return;
      if (!await confirm({
        title: t("project.phase.select"),
        body: t("project.phase.confirm", {
          previous: current?.name ?? t("project.phase.none"),
          selected: selected.label,
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
