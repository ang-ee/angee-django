# Directory synchronization

A Directory composes the [record-sync driver](../integrate/README.md). Each
address book maps to a Folder and a contacts stream. The Folder names the
collection; SyncStream owns progress and generations; RecordLink owns membership,
external identity, transport versions and retained wire payloads. Canonical Party
rows carry domain values, independent of any one directory.

The [CardDAV backend](../parties_integrate_carddav/backend.py) implements bounded
listing/multiget baselines, token deltas, href relocation, per-record quarantine,
conditional writes and enumeration sweeps. Both sides compare the shared
[contact projection](backends.py), including prepared avatar content addresses.
Wire properties outside that projection remain in RecordLink history and survive
conditional writes. Link identity is stable across later reads and generations;
local publication targets explicitly enrolled links rather than a Party column.

[PartyManager.ingest_contact](managers.py) asserts identity/contact and Person
name/date aspects for the directory principal. Address and Relationship retain
canonical identities and have their own statement tables. Missing source members
withdraw that directory statement; no address replacement or employment deletion
can erase another source or a person decision. Their canonical scopes include
rows without statements and exclude trash and wholly withdrawn assertions. Relationship retains
free-text counterparties, date bounds and its kind end validation.

People edit aspect values through ordinary saves, producing normal PERSON
statements. A person confirm/dismiss is preferred and always wins. Consumers
contribute their own tax aspects through declared donors;
the framework supplies no tax vocabulary. Precedence groups win together; losing
statements remain inspectable.

A remote tombstone withdraws source assertions and keeps person preferences.
A confirmed handle therefore survives card removal and can be republished;
dismiss makes the removal stick. The existing stream deletion and conflict
owners govern remote conditional DELETE and write-back. Reverting local database
state never reverses a completed HTTP request: the next sync compares the corrected
projection and obeys the usual remote preconditions and failure policy.

Handle producers use `Party.statements`: each persisted source owns its assertion
on a Party and typed Handle member. `PartyManager.assert_handle` admits a normal
statement; the generated manager composes the shared Handle, Party lock order.
Rules have persisted `provenance.Rule` identities. Social exporters must supply
their owning IMPORT Integration as `asserted_by`; a text provenance label cannot
identify a source. An authenticated person's own handle entry is a normal PERSON
statement and preserves control ownership, including contested 0.3 claims.

Confirm and dismiss write preferred PERSON statements without rewriting the
producer. Survivorship excludes normal claims below the review threshold. The
cross-party winner favors preferred rank, then
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

Source admission follows retained RecordLinks and resource-granted Rule/Seed
role membership. Producers never grant themselves per-record access.
Statement sources and migrated carrier readers gain no private-field reads.
Origin readability belongs to the person at the bounded evidence surface.
Import adapters must provide observed source links before asserting on a subject.
`ingest_connection` takes `link=`; `ingest_connections` consumes `(parsed, link)`
pairs. The owning import adapter supplies those observed links; parties binds
each canonical target inside its application transaction.

Identity matching writes normal RULE same_as statements on the ordered Party
pair. A preferred no_value is the durable symmetric anti-link. A preferred value
is a pending merge until a person chooses a survivor. The existing survivor.merge
verb owns the merge changeset, with the survivor as origin; survivorship selects
statements and never merges rows. Pointing-owner merge_contract unions statements,
retaining collisions as superseded, and Party keeps the merged redirect.

The phase 4 upgrade follows the handle cutover with party_aspect_schema,
fold_party_aspects and retire_party_carriers. The fold uses historical base
managers under its changeset without evaluating REBAC. Unproven source identities,
missing application evidence or dangling retired-resource pointers block by row.
The fold bootstraps needed principals on historical models and applies the
carrier's decisive stance. Recorded updated_by people own confirm/dismiss decisions;
missing actors block with their rows and count. Unattributed scalar values stay
on canonical columns without invented assertions.
Retirement drops the directory columns, Relationship source label and veto table
only after retained statement/link parity. Reversal restores empty legacy schema;
it does not recreate competing provenance stores.
