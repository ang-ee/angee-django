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
 * Stage is deliberately absent. Stages are queue-scoped, and a bare relation
 * picker is not: it would list every queue's stages, and the server rejects a
 * foreign one (`validate_stage_scope`, "Stage must belong to the record's
 * container") only at submit. Offering a choice that is rejected on save is a
 * worse dialog than not offering it. The board and cycle presets still place a
 * card in its lane -- they pass `stage` through `createDefaults`, which does not
 * need a declared field.
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
    <Field name="assignee" />
    <Field name="priority" />
    <Field name="due_date" />
    <Field name="estimate" />
  </>
);
