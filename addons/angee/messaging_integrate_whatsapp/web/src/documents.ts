import { graphql } from "@angee/gql/console";

// The WhatsApp bridge owns the connect/pairing verbs because linking creates a
// channel whose credential IS the device pairing (no secret is ever typed).
// The base channel list/detail stay model-driven. The addon-owned pairing query
// merges durable account identity with transient QR/session progress, while its
// `models` registration rides channelChanged without client polling.
export const ConnectWhatsappChannel = graphql(`
  mutation ConnectWhatsappChannel($name: String!) {
    connect_whatsapp_channel(name: $name) {
      id
      lifecycle
      runtime_status
    }
  }
`);

export const WhatsappChannelPairing = graphql(`
  query WhatsappChannelPairing($id: ID!) {
    whatsapp_pairing(id: $id) {
      state
      qr
      phone
      duplicate_channel_name
    }
  }
`);
