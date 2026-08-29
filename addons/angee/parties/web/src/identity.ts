// Party identity is resolved on ingest but only *shown* once a human confirms the
// handle→party link. Every surface whose sender *exposes party backedges* needs the
// same precedence, so the rule lives here — parties owns the identity vocabulary — and
// each such surface composes it instead of hand-rolling the ternary (which drifts: a
// timeline or transcript that forgets it shows the raw envelope name forever).
//
// Record chatter is deliberately NOT one of those surfaces: its sender projection
// (messaging's `RecordHandleType`) withholds `party`/`party_link_confirmed` as an
// authorization boundary, so it renders the envelope name directly and never calls
// this helper. `SenderIdentity`'s optional fields degrade to the envelope, so a caller
// that only has an envelope sender still gets a correct name.

/** A message sender as the messaging surfaces project it: the frozen channel
 *  envelope (`display_name`/`value`) plus, once the inbound handle is confirmed to
 *  a party, that party's curated `display_name`. Structural so any GraphQL-typed
 *  sender selection satisfies it. */
export interface SenderIdentity {
  display_name?: string | null;
  value?: string | null;
  party_link_confirmed?: boolean | null;
  party?: { display_name?: string | null } | null;
}

/** The one rule for a sender's shown name: the curated party name wins once the
 *  handle→party link is confirmed; otherwise the frozen envelope display name, then
 *  the raw address, then `fallback`. Compose this on every sender-render site so the
 *  precedence never diverges per view. */
export function senderDisplayName(
  sender: SenderIdentity | null | undefined,
  fallback = "",
): string {
  const partyName = sender?.party_link_confirmed ? sender.party?.display_name : null;
  return partyName || sender?.display_name || sender?.value || fallback;
}
