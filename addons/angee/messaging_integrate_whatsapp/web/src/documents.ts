import { graphql } from "@angee/gql/console";

// WhatsApp owns channel creation; pairing state and lifecycle verbs are shared
// by every live messaging backend through @angee/messaging.
export const ConnectWhatsappChannel = graphql(`
  mutation ConnectWhatsappChannel($name: String!) {
    connect_whatsapp_channel(name: $name) {
      id
      lifecycle
      runtime_status
    }
  }
`);
