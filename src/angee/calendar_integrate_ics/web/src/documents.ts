import { graphql } from "@angee/gql/console";

export const SubscribeIcsCalendar = graphql(`
  mutation SubscribeIcsCalendar($name: String!, $url: String!) {
    subscribe_ics_calendar(name: $name, url: $url) {
      id
      lifecycle
      runtime_status
    }
  }
`);
