import { Group, Field, RelativeTime, type StringIdRow } from "@angee/ui";
import * as React from "react";

import { estimateLabel } from "./estimates";
import { useWorkT } from "./i18n";

export interface WorkTaskRow extends StringIdRow {
  title?: unknown;
  work_key?: unknown;
  estimate?: unknown;
  priority?: unknown;
  due_date?: unknown;
  assignee?: unknown;
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

function WorkDetailSectionLabel(): React.ReactElement {
  const t = useWorkT();
  return <>{t("task.group.workDetail")}</>;
}

/**
 * Work-owned donor fields contributed into the projects-owned task FormView.
 *
 * Split by how often they are read, not by who owns them. Where a task sits in
 * the flow -- its queue, stage, cycle and size -- is checked on every visit, so
 * it goes in the standing properties column beside status; the identifiers and
 * triage timestamps are a long tail and stay in the body. A host form on any
 * other layout ignores the placement and renders both as ordinary sections.
 */
export const taskWorkFormSection = (
  <Group label={<WorkSectionLabel />} columns={1} placement="properties">
    <Field name="queue" readOnly />
    <Field name="stage" readOnly />
    <Field name="cycle" readOnly />
    <Field name="estimate" />
  </Group>
);

export const taskWorkDetailSection = (
  <Group label={<WorkDetailSectionLabel />} columns={2}>
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
      <CardFooter task={task} />
    </div>
  );
}

/**
 * Who, how urgent, by when -- the three facts a board is read for, and the three
 * the card did not carry. They were on the list all along, so the board was the
 * one view of a task that could not answer "is this mine and is it late?".
 *
 * Each part renders only when the row has it, so a card never shows an empty
 * slot, and the footer disappears entirely for a task with none of the three.
 */
function CardFooter({ task }: { task: WorkTaskRow }): React.ReactElement | null {
  const t = useWorkT();
  const assignee = displayName(task.assignee);
  // `NONE` is the absence of a priority, not a priority. Most seed cards carry
  // it, so rendering it put the word "NONE" on nearly every card and drowned the
  // few that say URGENT -- the opposite of what a priority is on a board for.
  const priority = meaningfulPriority(task.priority);
  const due = presentText(task.due_date);
  if (!assignee && !priority && !due) return null;
  return (
    <div className="flex items-center gap-2 text-xs text-fg-muted">
      {assignee ? (
        <span
          className="grid size-5 shrink-0 place-content-center rounded-full bg-inset text-[10px] font-semibold text-fg-subtle"
          title={assignee}
          aria-label={assignee}
        >
          {initials(assignee)}
        </span>
      ) : null}
      {priority ? (
        <span className="truncate rounded-6 bg-inset px-1.5 py-0.5">{priority}</span>
      ) : null}
      {due ? (
        <span className="ml-auto shrink-0 tabular-nums">
          <RelativeTime value={due} fallback={t("task.card.noDue")} />
        </span>
      ) : null}
    </div>
  );
}

/** The enum's "no priority" member, which is not worth a pill. */
function meaningfulPriority(value: unknown): string {
  const text = presentText(value);
  return text.toUpperCase() === "NONE" ? "" : text;
}

/** A relation renders as its representation object; a bare id is not a name. */
function displayName(value: unknown): string {
  if (value == null || typeof value !== "object") return "";
  const record = value as Record<string, unknown>;
  return presentText(record.display_name ?? record.name ?? record.title);
}

function presentText(value: unknown): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value).trim()
    : "";
}

function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length === 0) return "";
  const first = words[0]?.[0] ?? "";
  const last = words.length > 1 ? (words.at(-1)?.[0] ?? "") : "";
  return `${first}${last}`.toUpperCase();
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
