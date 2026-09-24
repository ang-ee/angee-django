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
a new baseline generation, retaining links and revisions. Explicit removed-member
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
corrected card to resolve the same quarantine. Applied revisions retain the
resource href separately with the vCard version used for conditional writes.
Its target is the Person produced by `Party.objects.ingest_contact`; shared
contact points still use `Handle.objects.upsert`. A local person added to a synced
folder receives a stable `angee-<primary key>` remote UID on its first push. A
later pull reuses that person through the same ingest owner.

[`contact_projection`](backends.py) declares exactly the fields synchronized in
both directions. It includes names, notes, dates, email/phone contact points,
postal addresses, the source employment edge and the avatar content hash and MIME
type. Extraction fetches and pre-stores avatars through storage's idempotent
`File.objects.ingest_bytes` before the page transaction. Apply only validates and
links the prepared File under the row lock; it performs database work only.
Local comparisons use the stored hash without reading avatar bytes, and revisions
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

Core parties models do not carry `ExternalOwnershipMixin`'s provenance columns.
The stream therefore records its mapped field ownership and compare-to-base
enforcement in `config.field_ownership`; provenance guard adoption is deferred.
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
