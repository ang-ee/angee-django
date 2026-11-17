import { graphql } from "@angee/gql/console";

// The calendar page reads what it shows and the calendars it labels it by. Recurrence
// is expanded server-side, so `event_occurrences` already returns the occurrence
// wire shape; each occurrence names its calendar, and a `redacted` one is busy time
// the viewer may not read. The drag verb is the derived `reschedule_event` action.

export const EventOccurrencesDocument = graphql(`
  query CalendarEventOccurrences($window_start: DateTime!, $window_end: DateTime!) {
    event_occurrences(window_start: $window_start, window_end: $window_end) {
      occurrence_id
      event_sqid
      calendar
      title
      start
      end
      all_day
      editable
      redacted
    }
  }
`);

export const CalendarListDocument = graphql(`
  query CalendarList {
    calendars(order_by: [{ name: asc }], limit: 200) {
      id
      name
    }
  }
`);
