import type { ReactElement } from "react";
import { Field } from "@angee/ui";

/**
 * The create form for a task, registered as the `projects.Task` form override so
 * it is used wherever a task is created — the global New task, the board's
 * in-lane Add card, a project's Tasks tab, and a relation picker's inline create.
 *
 * The record form is tabbed, because a task carries a long tail: ordering,
 * recurrence, the drop reason, the release it shipped in. Creating one needs
 * almost none of that, and the tabbed form put five tab labels in front of the
 * first field. This is the same `Form` vocabulary, one column, in the order the
 * fields are actually filled: what it is, then where it goes, then who and when.
 *
 * Every name here is in `project_tasks_insert_input`, so nothing declared is
 * uncreatable. Labels and enum options are left to the SDL metadata rather than
 * hard-coded (`fieldsWithMetadataDefaults` fills options for a bare enum field),
 * so a schema rename reaches this form without editing it.
 */
export const taskCreateForm: ReactElement = (
  <>
    <Field name="title" title />
    <Field name="note" widget="textarea" />
    <Field name="project" />
    <Field name="queue" />
    <Field name="stage" />
    <Field name="assignee" />
    <Field name="priority" />
    <Field name="due_date" />
    <Field name="estimate" />
  </>
);
