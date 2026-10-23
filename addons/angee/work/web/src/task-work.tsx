import { Group, Field, RelativeTime, type StringIdRow } from "@angee/ui";
import * as React from "react";

import { useWorkT } from "./i18n";

export interface WorkTaskRow extends StringIdRow {
  title?: unknown;
  work_key?: unknown;
  estimate?: unknown;
  queue?: unknown;
  stage?: { category?: string | null } | null;
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
