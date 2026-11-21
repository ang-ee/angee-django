import { graphql } from "@angee/gql/console";

/** The signed-in user's Google accounts a Google calendar source reads with, and how another one connects. */
export const GoogleCalendarAccounts = graphql(`
  query GoogleCalendarAccounts {
    calendar_source_backend(backend_class: "google") {
      ...ConnectWithAccountFields
    }
  }
`);

export const ConnectGoogleCalendar = graphql(`
  mutation ConnectGoogleCalendar($credentialId: ID!) {
    connect_google_calendar(credential_id: $credentialId) {
      id
      lifecycle
      runtime_status
    }
  }
`);
