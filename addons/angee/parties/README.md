# Directory synchronization

A Directory is an Integration child driven by the shared
[record-sync driver](../integrate/README.md). Its backend discovers address books,
each mapped to one Folder and one `contacts` stream whose partition is the
collection href. `SyncStream` owns progress and generations; Folder carries only
collection identity and display metadata.

The CardDAV backend takes a full listing and bounded multiget baseline, then
uses [RFC 6578 sync-collection](https://www.rfc-editor.org/rfc/rfc6578.html) deltas.
It captures the collection token before listing so edits during a baseline replay
on the next delta. Pending hrefs and the next token live only in the stream cursor.
An unchanged token produces an empty page. `DAV:valid-sync-token` failure creates
a new baseline generation, retaining links and their history. Explicit removed-member
404 responses become tombstones; a failed transport never advances the cursor.
Per-identity discrepancy rescans use CardDAV multiget without advancing that
cursor. A daily depth-one PROPFIND sweep seeks in href order and reads bounded
multiget pages through the same apply path, importing contacts missed by the
change feed. The driver checkpoints each page before counting absent resources;
callers pulse until `SyncStream.reconcile_state` is empty. A silently
vanished resource becomes unavailable even when the change feed omits it.

`RecordLink.external_key` preserves the existing `(folder, source_uid)` identity:
vCard UID, falling back to href for legacy cards without UID. A newly enumerated
href also becomes its identity before a sweep can read its UID; parsed UIDs stay
in link metadata. A resource keeps that first identity on later reads and when
a delta relocates its UID. Duplicate UIDs quarantine the colliding card while
other cards proceed. Observed link metadata retains the href even when the first
card is malformed, allowing a
corrected card to resolve the same quarantine. Applied link history retains the
resource href separately with the vCard version used for conditional writes.
Its target is the Person produced by `Party.objects.ingest_contact`; shared
contact points still use `Handle.objects.upsert`. A local person added to a synced
folder receives a stable `angee-<primary key>` remote UID on its first push. A
later pull reuses that person through the same ingest owner.

On the first baseline, existing people matched by `(folder, source_uid)` use the
driver's [baseline adoption rule](../integrate/README.md). Local-only people can
be created remotely as soon as that baseline completes, including in the same
sync call. Before upgrading a pull-only Directory, remove people that must remain
local from synchronized folders.

[`contact_projection`](backends.py) declares exactly the fields synchronized in
both directions. It includes names, notes, dates, email/phone contact points,
postal addresses, the source employment edge and the avatar content hash and MIME
type. Email and phone contact points come only from the person's
[applied handle statements](managers.py): those the card itself carries, those a person
added, and confirmed suggestions. Unreviewed suggestions such as mined
signature phones or display-name matches stay inside Angee and are never
written to a card. Extraction fetches and pre-stores avatars through storage's
idempotent `File.objects.ingest_bytes` before the page transaction. Apply only
validates and links the prepared File under the row lock; it performs database
work only.
Local comparisons use the stored hash without reading avatar bytes, and link history
retain the content address instead of base64 photo bytes. Collection order
does not affect hashes. The remote and local bases are separate because domain
fields may normalize values. Transport metadata, unmapped ORG department units,
and other vCard properties remain outside the comparison. Conditional writes
preserve those unmapped properties in the retained vCard.

Each cycle compares local projections with their last applied bases. Remote-only
edits use the existing ingest verb; local-only edits use a conditional PUT with
the resource ETag as `If-Match`, following
[CardDAV's resource write contract](https://www.rfc-editor.org/rfc/rfc6352.html#section-6.3.2).
New resources use `If-None-Match: *`. Successful writes refresh both bases and the
remote version and retain origin `local`, so the next pull recognizes the write.
Both-sided changes and HTTP 412 responses remain unresolved `CONFLICT`
discrepancies until the operator chooses Keep remote or Keep local. Keeping the
remote re-reads and applies that record; keeping the local re-reads the remote
version before a conditional write. A newer remote edit can reject that write
and leave the conflict open.

Deletion defaults preserve data: remote deletion tombstones the link and retains
the local person; local deletion while the remote remains live produces a
conflict. The initial stream policy can be seeded per collection:

```json
{
  "streams": {
    "contacts": {
      "https://dav.example/books/contacts/": {
        "remote_delete": "retain",
        "local_delete": "conflict"
      }
    }
  }
}
```

Either policy may explicitly be `propagate`. Local propagation uses conditional
DELETE with `If-Match`; remote propagation deletes the unchanged local party.
An edited retained tombstone cannot silently recreate the remote. Existing
streams own their persisted `config`; changing a Directory's seed does not
overwrite an existing policy. Epoch changes preserve that configuration.

Contact fields remain locally editable; the stream's comparison bases and
conflict policy govern synchronization with the remote address book.
The Integration owner supplies the credential and the local audit owner.

Malformed cards are quarantined individually. Unsupported or malformed DAV
responses fail the page without skipping its cursor. A server must implement
RFC 6578 and strong per-resource ETags for this bidirectional backend. Basic
authentication remains supported; Digest authentication needs a credential
handler. Tests in `tests/test_parties_carddav_sync.py` exercise the real adapter,
driver and ingest against a deterministic DAV server double.

The public GraphQL `ContactFolderType.ctag` field was removed when collection
progress moved to `SyncStream`; clients must remove it from their selections.
The repository's addon, package and example web sources have no remaining reads.


Handle producers use `Party.statements`: each persisted source owns its assertion
on a Party and typed Handle member. `PartyManager.assert_handle` admits a normal
statement; the generated manager composes the shared Handle, Party lock order.
Rules have persisted `provenance.Rule` identities. Social exporters must supply
their owning IMPORT Integration as `asserted_by`; a text provenance label cannot
identify a source. An authenticated person's own handle entry is a normal PERSON
statement and preserves control ownership, including contested 0.3 claims.

Confirm and dismiss write preferred PERSON statements without rewriting the
producer. A confirmation survives withdrawal by a directory, so write-back can
republish it; dismiss makes removal stick. Survivorship excludes normal claims
below the review threshold. The cross-party winner favors preferred rank, then
confidence, a real-name tie-break, and the shared recency/identity order.
Resolution and both Party counters update in that transaction.

Review and Identity project one current stance per Party/Handle member through
Party authorization; the Party's statements accessor retains all independent
sources. Handles and bounded origin evidence retain their own read gates.
Statement IDs use `pst_`; the retired `phl_` resource and routes are gone.
Circle and spaces memberships are plain acceptance/rejection rows, not statement
sources; their source labels retire while scores and decisions remain.

Upgrade order is `party_statement_schema` → `fold_party_handle_statements` →
`circle_member_decisions` → `retire_party_handle`. Spaces separately declares
`membership_decisions`. Retirement refuses missing fold evidence and migrates
retained workflow references to statement IDs and subject concern records.
Pre-cutover frozen identity hashes require fresh review. Migration reversals do
not recreate discarded carrier metadata or roster source labels.

Trusted matchers grant their persisted source explicit reads of the supporting
Party/Handle records before assertion admission. Claimed-sender origins must
admit the RULE principal on their native reader relation; messaging declares
that source type. Human decisions cite only bounded origins the person can read.
