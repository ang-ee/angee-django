import { extractActionOutcome, useAuthoredQuery } from "@angee/refine";
import { holdsPermission } from "@angee/metadata";
import {
  ErrorBanner,
  Skeleton,
  SkeletonStatus,
  RelationPicker,
  useActionResultRun,
  useAuthoredResourceMutation,
  useConfirm,
  useRelationOptions,
} from "@angee/ui";
import * as React from "react";

import { ProjectPhaseDocument, SetProjectCurrentMilestoneDocument } from "./documents";
import { useProjectsT } from "./i18n";
import { MILESTONE_MODEL, PROJECT_MODEL, TASK_MODEL } from "./resources";

const milestoneRelation = { resource: MILESTONE_MODEL, labelField: "name", canCreate: false };

/** Select one server-eligible phase, confirming the previous and next names. */
export function ProjectPhaseControl({ recordId }: { recordId: string }): React.ReactElement {
  const t = useProjectsT();
  const confirm = useConfirm();
  const settle = useActionResultRun();
  const query = useAuthoredQuery(ProjectPhaseDocument, { id: recordId }, {
    models: [PROJECT_MODEL, MILESTONE_MODEL],
    records: [{ model: PROJECT_MODEL, id: recordId }],
  });
  const project = query.data?.projects_by_pk;
  const canWrite = holdsPermission(project, "write");
  const eligibleIds = canWrite ? project?.selectable_milestones.map((row) => row.id) ?? [] : [];
  const { options, list } = useRelationOptions(milestoneRelation, {
    enabled: eligibleIds.length > 0,
    filters: [{ field: "id", operator: "in", value: eligibleIds }],
    sorters: [{ field: "sort_order", order: "asc" }],
  });
  const [selectPhase, state] = useAuthoredResourceMutation(SetProjectCurrentMilestoneDocument, {
    invalidateModels: [PROJECT_MODEL, TASK_MODEL],
    shouldInvalidate: (data) => data?.set_project_current_milestone.ok === true,
  });
  if (query.isFetching && !project) return <SkeletonStatus label={t("project.phase.loading")}>
    <Skeleton className="h-8 w-52" />
  </SkeletonStatus>;
  if (query.error || !project) return <ErrorBanner description={t("project.phase.unavailable")} />;
  return <RelationPicker
    aria-label={t("project.phase.select")}
    value={project.current_milestone?.id ?? ""}
    options={eligibleIds.length > 0 ? options : []}
    placeholder={project.current_milestone?.name ?? t("project.phase.none")}
    readOnly={!canWrite || state.fetching || query.isFetching || eligibleIds.length === 0}
    searchState={{ pending: list.fetching, error: list.error, retry: list.refetch }}
    onChange={async (id) => {
      const selected = options.find((option) => option.value === id);
      if (!selected) return;
      if (!await confirm({
        title: t("project.phase.select"),
        body: t("project.phase.confirm", {
          previous: project.current_milestone?.name ?? t("project.phase.none"),
          selected: selected.label,
        }),
      })) return;
      await settle(async () => extractActionOutcome(await selectPhase({
        id: recordId,
        milestone: id,
        expected_revision: project.revision,
      }), "set_project_current_milestone"));
    }}
  />;
}
