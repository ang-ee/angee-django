import { graphql } from "@angee/gql/console";
import * as v from "valibot";

// The WhatsApp bridge owns the connect/pairing verbs because linking creates a
// channel whose credential IS the device pairing (no secret is ever typed).
// The base channel list/detail stay model-driven; the pairing read is a plain
// authored query over the channel row — its `models` registration rides the
// existing channelChanged live bridge, so it refetches on every session report
// without any client polling.
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
  query WhatsappChannelPairing($id: String!) {
    channels_by_pk(id: $id) {
      id
      sync_progress
    }
  }
`);

export const ResetWhatsappPairing = graphql(`
  mutation ResetWhatsappPairing($id: ID!) {
    reset_whatsapp_pairing(id: $id) {
      ok
      message
    }
  }
`);

export const DisconnectWhatsappChannel = graphql(`
  mutation DisconnectWhatsappChannel($id: ID!) {
    disconnect_whatsapp_channel(id: $id) {
      ok
      message
    }
  }
`);

// The pairing payload the live session mirrors into `sync_progress.details.pairing`.
// It crosses the GraphQL `JSON` scalar, so the shape is opaque on the wire and must
// be parsed (not asserted) at the network boundary — `PairingSchema` does that, and
// the states mirror `PairingState` in the addon's `client.py`; keep the two in step.
const PairingSchema = v.object({
  state: v.picklist(["starting", "awaiting_scan", "paired", "logged_out", "stopped"]),
  qr: v.optional(v.string()),
  jid: v.optional(v.string()),
  phone: v.optional(v.string()),
});

export type WhatsappPairing = v.InferOutput<typeof PairingSchema>;

const SyncProgressSchema = v.object({
  details: v.optional(v.object({ pairing: v.optional(PairingSchema) })),
});

/** Parse a channel row's opaque `sync_progress` JSON into the pairing payload. */
export function pairingFromSyncProgress(syncProgress: unknown): WhatsappPairing {
  const parsed = v.safeParse(SyncProgressSchema, syncProgress);
  return parsed.success && parsed.output.details?.pairing
    ? parsed.output.details.pairing
    : { state: "starting" };
}
