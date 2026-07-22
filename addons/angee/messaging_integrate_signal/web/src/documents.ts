import { graphql } from "@angee/gql/console";

// Signal owns channel creation; pairing state and lifecycle verbs are shared
// by every live messaging backend through @angee/messaging.
export const ConnectSignalChannel = graphql(`
  mutation ConnectSignalChannel {
    connect_signal_channel {
      id
      lifecycle
      runtime_status
    }
  }
`);
