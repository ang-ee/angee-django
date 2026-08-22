import { Group, Field, RelativeTime, type StringIdRow } from "@angee/ui";
import * as React from "react";

import { estimateLabel } from "./estimates";
import { useWorkT } from "./i18n";

export interface WorkTaskRow extends StringIdRow {
  title?: unknown;
  work_key?: unknown;
  estimate?: unknown;
  queue?: unknown;
  stage?: unknown;
  cycle?: unknown;
  started_triage_at?: unknown;
  triaged_at?: unknown;
}

function WorkSectionLabel(): React.ReactElement {
  const t = useWorkT();
  return <>{t("task.group.work")}</>;
}

/** Work-owned donor fields contributed into the projects-owned task FormView. */
export const taskWorkFormSection = (
  <Group label={<WorkSectionLabel />} columns={2}>
    <Field name="queue" readOnly />
    <Field name="stage" readOnly />
    <Field name="cycle" readOnly />
    <Field name="estimate" />
    <Field name="number" readOnly />
    <Field name="snoozed_until" readOnly />
    <Field name="snoozed_by" readOnly />
    <Field name="started_triage_at" readOnly />
    <Field name="triaged_at" readOnly />
  </Group>
);

export function WorkTaskCard({
  task,
  estimateScale,
}: {
  task: WorkTaskRow;
  estimateScale: unknown;
}): React.ReactElement {
  const t = useWorkT();
  const estimate = estimateLabel(task.estimate, estimateScale, t);
  const workKey = String(task.work_key ?? "").trim();
  const title = String(task.title ?? "").trim();
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-3 text-xs text-fg-muted">
        <span className="font-semibold tabular-nums">
          {workKey || t("task.card.unnumbered")}
        </span>
        {estimate ? <span className="tabular-nums">{estimate}</span> : null}
      </div>
      <span className="text-sm font-medium text-fg">{title}</span>
    </div>
  );
}

export function TriageDwell({ value }: { value: unknown }): React.ReactElement {
  const t = useWorkT();
  return (
    <RelativeTime
      value={typeof value === "string" || typeof value === "number" ? value : null}
      addSuffix={false}
      fallback={t("triage.dwell.empty")}
    />
  );
}

/** Triage lifecycle facts, not a stage-name probe, own action visibility. */
export function isTaskInTriage(record: WorkTaskRow): boolean {
  return record.started_triage_at != null && record.triaged_at == null;
}
