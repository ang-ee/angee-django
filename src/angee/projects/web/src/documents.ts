import { graphql, type DocumentType } from "@angee/gql/console";

/** A phase selection carries the revision shown when the choice was made. */
export const SetProjectCurrentMilestoneDocument = graphql(`
  mutation ProjectsSetCurrentMilestone($id: ID!, $milestone: ID!, $expected_revision: Int) {
    set_project_current_milestone(id: $id, milestone: $milestone, expected_revision: $expected_revision) {
      ok message validation_errors id
    }
  }
`);

/** Audience changes use the task's dedicated permission and revision guard. */
export const SetTaskVisibilityDocument = graphql(`
  mutation ProjectsSetTaskVisibility($id: ID!, $visibility: TaskVisibility!, $expected_revision: Int) {
    set_task_visibility(id: $id, visibility: $visibility, expected_revision: $expected_revision) {
      ok message validation_errors id
    }
  }
`);

// Record tasks: the task schema derives their access from the record. The generic
// task verbs complete and drop them; a completion or a logged exchange posts on the
// record's chatter, so the chatter's reads refresh with the task's.
export const RECORD_TASK_MODELS = ["projects.Task", "projects.ActivityType", "messaging.Message"] as const;

export const RecordTaskFields = graphql(`
  fragment RecordTaskFields on ConsoleTaskType {
    id
    title
    note
    status
    due_date
    done_at
    activity_type
    assignee { id username display_name }
    task_actions
  }
`);

export const RecordTasksDocument = graphql(`
  query ProjectsRecordTasks($modelLabel: String!, $recordId: ID!) {
    record_tasks(model_label: $modelLabel, record_id: $recordId) { ...RecordTaskFields }
    activity_types { id key name glyph }
  }
`);

export const ScheduleRecordTaskDocument = graphql(`
  mutation ProjectsScheduleRecordTask(
    $modelLabel: String!, $recordId: ID!, $title: String!, $note: String = "", $dueDate: Date,
    $activityType: String
  ) {
    schedule_record_task(input: {
      model_label: $modelLabel, record_id: $recordId, title: $title, note: $note, due_date: $dueDate,
      activity_type: $activityType
    }) { ok message validation_errors id }
  }
`);

export const LogRecordTaskDocument = graphql(`
  mutation ProjectsLogRecordTask(
    $modelLabel: String!, $recordId: ID!, $activityType: String!, $occurredOn: Date!, $note: String!
  ) {
    log_record_task(input: {
      model_label: $modelLabel, record_id: $recordId, activity_type: $activityType, occurred_on: $occurredOn,
      note: $note
    }) { ok message validation_errors id }
  }
`);

export type RecordTasksPayload = DocumentType<typeof RecordTasksDocument>;
export type RecordTaskRow = RecordTasksPayload["record_tasks"][number];

// The viewer's open tasks due in a calendar window, overlaid on the calendar page.
// `Date`-typed bounds: a due date is a calendar day, never pinned to UTC midnight.
export const CalendarDueTasksDocument = graphql(`
  query ProjectsCalendarDueTasks($assignee: String!, $window_start: Date!, $window_end: Date!) {
    project_tasks(
      where: {
        assignee: { _eq: $assignee }
        status: { _eq: "OPEN" }
        due_date: { _gte: $window_start, _lt: $window_end }
      }
      order_by: [{ due_date: asc }]
      limit: 500
    ) {
      id
      title
      due_date
      subject {
        label
        model_label
        record_id
      }
    }
  }
`);

export type CalendarDueTaskRow = DocumentType<typeof CalendarDueTasksDocument>["project_tasks"][number];
