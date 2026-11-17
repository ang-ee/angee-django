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
      party {
        display_name
      }
    }
  }
`);

// The event form's calendar choice: the calendars the viewer may write, at most two,
// which tells none (offer their own), one (the default) and a choice apart.
export const WritableCalendarsDocument = graphql(`
  query WritableCalendars {
    calendars(where: { writable: { _eq: true } }, order_by: [{ name: asc }], limit: 2) {
      id
    }
  }
`);

// The viewer's own calendar, created on first use. A verb without arguments is not a
// derived action (codegen derives only those with arguments), so it stays authored.
export const EnsurePersonalCalendarDocument = graphql(`
  mutation EnsurePersonalCalendar {
    ensure_personal_calendar {
      ok
      message
      id
      validation_errors
    }
  }
`);
