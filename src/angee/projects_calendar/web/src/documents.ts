import { graphql, type DocumentType } from "@angee/gql/console";

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
