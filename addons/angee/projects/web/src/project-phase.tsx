import { holdsPermission, refineResourceName, useModelMetadata } from "@angee/metadata";
import { extractActionOutcome, refineFieldsFromPaths } from "@angee/refine";
import {
  Button,
  DatePopover,
  StatusbarSkeleton,
  StatusbarSteps,
  dateFromValue,
  formatDate,
  formatDateStorage,
  optionLabel,
  relationValueId,
  useActionResultRun,
  useAuthoredResourceMutation,
  useConfirm,
  useEnumOptions,
  useRelationOptions,
  useRuntimeViewAs,
  type WidgetRenderProps,
} from "@angee/ui";
import { useUpdate } from "@refinedev/core";
import * as React from "react";

import { SetProjectCurrentMilestoneDocument } from "./documents";
import { useProjectsT } from "./i18n";
import { MILESTONE_MODEL, PROJECT_MODEL, TASK_MODEL } from "./resources";

const milestoneRelation = { resource: MILESTONE_MODEL, labelField: "name", canCreate: false };

type PhaseRecord = {
  id?: string;
  revision?: number;
  permissions?: readonly string[];
  status?: string;
  on_path?: boolean;
  status_changed_at?: string;
  current_milestone?: { id: string; name: string } | null;
  selectable_milestones?: readonly { id: string }[];
};

type MilestoneRow = {
  id?: string;
  name?: string;
  description?: string;
  start_date?: string | null;
  target_date?: string | null;
  revision?: number;
  permissions?: readonly string[];
};

/** The project's milestone-owned phase and dates. */
export function ProjectPhaseControl({ value, row, field, readOnly = false }: WidgetRenderProps<unknown, PhaseRecord>): React.ReactElement | null {
  const t = useProjectsT();
  const preview = useRuntimeViewAs();
  const confirm = useConfirm();
  const settle = useActionResultRun();
  const recordId = row?.id;
  const canWrite = Boolean(recordId && !readOnly && !preview.viewAs && !preview.pending && holdsPermission(row, "write"));
  const selectable = new Set(canWrite ? row?.selectable_milestones?.map((item) => item.id) ?? [] : []);
  const { options, rows, list } = useRelationOptions(milestoneRelation, {
    enabled: Boolean(recordId),
    fields: ["description", "start_date", "target_date", "revision", "permissions"],
    filters: recordId ? [{ field: "project", operator: "eq", value: recordId }] : [],
    sorters: [{ field: "sort_order", order: "asc" }],
  });
  const metadata = useModelMetadata(MILESTONE_MODEL);
  const resource = metadata?.resource ?? null;
  const update = useUpdate({
    resource: refineResourceName(resource),
    dataProviderName: resource?.schemaName,
    meta: { fields: refineFieldsFromPaths(["id", "start_date", "target_date", "revision", "permissions"]) },
    invalidates: ["list", "many", "detail"],
  });
  const canEditDates = !preview.viewAs && !preview.pending && !update.mutation.isPending;
  const [selectPhase, state] = useAuthoredResourceMutation(SetProjectCurrentMilestoneDocument, {
    invalidateModels: [PROJECT_MODEL, TASK_MODEL],
    shouldInvalidate: (data) => data?.set_project_current_milestone.ok === true,
  });
  const statusOptions = useEnumOptions(PROJECT_MODEL, "status");
  const [editingDates, setEditingDates] = React.useState<string | null>(null);
  const [openDate, setOpenDate] = React.useState<"start_date" | "target_date" | null>(null);
  if (!recordId) return null;
  if (list.fetching && options.length === 0) return <StatusbarSkeleton fill twoLine count={4} />;

  const milestones = rows as readonly MilestoneRow[];
  const byId = new Map(milestones.map((item) => [item.id, item]));
  const current = row?.current_milestone;
  const terminal = row?.on_path === false;
  const steps = options.map((option) => {
    const milestone = byId.get(option.value);
    return {
      ...option,
      onPath: true,
      selectable: selectable.has(option.value),
      startDate: milestone?.start_date,
      endDate: milestone?.target_date,
      note: milestone?.description ? t("project.phase.endsWith", { note: milestone.description }) : undefined,
      editableDates: canEditDates && holdsPermission(milestone, "write"),
    };
  });
  const dateRow = editingDates ? byId.get(editingDates) : undefined;
  const changeDate = (name: "start_date" | "target_date", date: Date | null) => {
    if (!editingDates || !dateRow || !canEditDates || !holdsPermission(dateRow, "write")) return;
    void update.mutateAsync({
      id: editingDates,
      values: { [name]: formatDateStorage(date) },
      meta: { fields: refineFieldsFromPaths(["id", "start_date", "target_date", "revision", "permissions"]),
        gqlVariables: { expected_revision: dateRow.revision } },
    }).then(() => {
      list.refetch();
      setOpenDate(null);
      setEditingDates(null);
    }).catch(() => undefined);
  };
  return <>
    <StatusbarSteps
      aria-label={t("project.phase.select")}
      steps={steps}
      value={relationValueId(value) || current?.id}
      fill={field?.fill ?? true}
      containerWidth={field?.containerWidth}
      readOnly={!canWrite || state.fetching || update.mutation.isPending}
      offPath={terminal ? {
        label: optionLabel(statusOptions, row?.status),
        date: row?.status_changed_at,
      } : undefined}
      onEditDates={setEditingDates}
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
          id: recordId, milestone: id, expected_revision: row?.revision,
        }), "set_project_current_milestone"));
      }}
    />
    {dateRow && editingDates && canEditDates && holdsPermission(dateRow, "write") ? <div
      className="mt-2 flex flex-wrap items-end gap-2 rounded-6 border border-border-subtle bg-sheet p-2 text-xs">
      <span className="w-full font-medium">{dateRow.name}</span>
      {(["start_date", "target_date"] as const).map((name) => <div key={name} className="min-w-36">
        <span className="mb-1 block text-fg-muted">{t(name === "start_date" ? "project.phase.starts" : "project.phase.ends")}</span>
        <DatePopover selected={dateFromValue(dateRow[name] ?? null)}
          label={formatDate(dateRow[name]) || t("project.phase.noneDate")}
          ariaLabel={t(name === "start_date" ? "project.phase.starts" : "project.phase.ends")}
          open={openDate === name} onOpenChange={(open) => setOpenDate(open ? name : null)}
          onSelectDate={(date) => changeDate(name, date)} />
      </div>)}
      <Button type="button" variant="ghost" size="sm" onClick={() => { setOpenDate(null); setEditingDates(null); }}>
        {t("project.phase.closeDates")}
      </Button>
    </div> : null}
  </>;
}
