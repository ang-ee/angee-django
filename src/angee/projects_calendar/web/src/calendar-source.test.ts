import type { ResourceRecordHrefLookup } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { dueTaskOccurrences, dueTasksCalendarSource } from "./calendar-source";
import type { CalendarDueTaskRow } from "./documents";

// A route owner that routes leads and tasks, and nothing else.
const recordHref: ResourceRecordHrefLookup = (model, id) =>
  model === "crm.Lead" ? `/crm/leads/${encodeURIComponent(id)}`
  : model === "projects.Task" ? `/projects/tasks/${encodeURIComponent(id)}`
  : undefined;

const TASK = {
  id: "tsk_1",
  title: "Call",
  due_date: "2026-06-15",
  subject: { model_label: "crm.Lead", record_id: "lead /one", label: "Lead" },
} satisfies CalendarDueTaskRow;

describe("due-task occurrences", () => {
  test("a task opens the record it is about, as an all-day read-only marker", () => {
    expect(dueTaskOccurrences(TASK, recordHref)).toEqual([{
      occurrence_id: "projects.task:tsk_1",
      event_sqid: "",
      title: "Call",
      start: "2026-06-15",
      end: "2026-06-15",
      all_day: true,
      editable: false,
      to: "/crm/leads/lead%20%2Fone",
    }]);
  });

  test("a subject whose model has no routed page stays inert", () => {
    const unrouted = { ...TASK, subject: { ...TASK.subject, model_label: "missing.Model" } };
    expect(dueTaskOccurrences(unrouted, recordHref)[0]?.to).toBeUndefined();
  });

  test("a task without a subject opens the task itself, and one without a due date places no marker", () => {
    expect(dueTaskOccurrences({ ...TASK, subject: null }, recordHref)[0]?.to).toBe("/projects/tasks/tsk_1");
    expect(dueTaskOccurrences({ ...TASK, due_date: null }, recordHref)).toEqual([]);
  });

  test("the source asks for the viewer's tasks on local days, and none while signed out", () => {
    const source = dueTasksCalendarSource({ viewerId: "usr_viewer", recordHref });
    const window = { start: new Date(2026, 5, 14), end: new Date(2026, 5, 21) };
    expect(source.variables(window)).toEqual({ assignee: "usr_viewer", window_start: "2026-06-14", window_end: "2026-06-21" });
    expect(dueTasksCalendarSource({ viewerId: "", recordHref }).enabled).toBe(false);
  });
});
