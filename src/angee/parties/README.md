# Directory synchronization

A Directory composes the [record-sync driver](../integrate/README.md). Its
`addressbooks` stream mirrors each discovered address book as a Folder: the
book's RecordLink targets the folder and is the container of that book's
`contacts` stream, whose partition is the book's key. The directory states the
folder's name through that link ([FolderManager.ingest_addressbook](managers.py)),
so a person's rename outranks a remote one. A book missing from the listing
pauses its contacts; once its absence is confirmed it retires its contacts
stream, whose contacts are tombstoned, while folders and parties stay. An empty
listing confirms nothing, and a refused discovery raises. `streams()` declares
from local state only. RecordLink owns
external identity and wire payloads. Party rows remain independent of any one
directory.

A [directory backend](backends.py) reads a book's contacts as payloads keyed by
external key (`DirectoryBackend.observe`, `read_contacts`, `contact_keys`); the
base turns them into records, compares the shared contact projection on both
sides and applies each through the ingest owners. The backend's `direction`
decides which way contacts sync, pull-only unless it declares otherwise.
`Directory` forwards its connection test and its credential rule to the backend:
`DirectoryBackend.credential_blocker` is the one rule integrate raises on every
attachment and the connect picker shows beside each account.
Connecting a directory is one verb, `Directory.objects.connect`, which a vendor's
connect mutation calls: it builds the credential and the directory unsaved, has
the backend admit the credential and test the connection outside any
transaction, refuses with the backend's own message before anything is saved,
then creates both atomically, claims the address the account signs in as
(`DirectoryBackend.account_address`) as the owner's own handle, and requests the
first sync. Each vendor's row is a master resource adopted by slug.
The [CardDAV backend](../parties_integrate_carddav/backend.py) syncs two-way and
builds its own records: a card's `UID` is its key and its href rides in the
link's metadata, and wire properties outside the projection stay in RecordLink
history and survive conditional writes.

On a pull-only stream the local side of each comparison is what the source last
applied, and the source's own projection becomes the applied base, as on the
address-book stream: a person's edit is a statement that outranks the source,
never a conflict, and an unchanged re-read applies nothing. A tombstone in a
page, or a record the absence sweep confirms gone, withdraws the link's
statements and keeps the party; a record that returns applies again. A payload
may name the records a merge at the source replaced, in the source's order
(`observe(..., replaces=...)`): the survivor lands on the first one's person, what
it restates moves to its link (statements are keyed by source, subject and
aspect), and what only a replaced record stated is withdrawn with the survivor's
own unsupported handles, addresses and employers. A replaced record is never a
record of the page: it leaves the source's key walk, so the absence sweep
tombstones its link. A two-way stream compares the person's own projection and
keeps a vanished contact's statements.

One function decides a contact's target (`DirectoryBackend._contact_target`),
once, under the page's and the contact's locks: its own link's person, then a
replaced link's, then, on a pull-only stream, an existing person one of its
emails names (`PartyManager.people_for_emails`, the preferred email first). A
page reads who its unlinked contacts' emails name once, and locks every spelling
of its contacts' addresses, the people they name and the parties its links name
before any record decides (`DirectoryBackend.prepare_page`). Adoption is an
access change, so the binding owner admits it: `PartyManager.adoption_blocker`
refuses unless the stream is pull-only, the person is no account's own, the
address that found them is no account's sign-in address, the directory's owner
holds `adopt` on them (their owner or an editor, with no platform-admin arm,
since every directory's owner is a platform admin; checked for that user though
sync runs as system work), no other live link of the directory names them, and no
live link of a stream that writes back names them. `ingest_contact` asks it for
every person new to its link that no replaced link names, so neither a vendor's
own `apply_record` nor another caller binds past it. Otherwise the contact mints
its own person; a two-way directory never adopts. The handle an adoption found
the person by is the evidence of the directory's identity statement on them,
restated on every later record, and the contact's spelling of that address lands
on the same handle row. Each person the decision passes over (a candidate
a limit refused, or another person a replaced record was linked to) is proposed
as the landed person's same person by parties' `directory_match` rule
(`PartyManager.propose_directory_match`), citing the contact's link and the
handle or the replaced link, and joins the same-as review. A pair kept separate
is not proposed again; merging the two, or the contact later adopting the
person it was refused, withdraws the proposal. The `same_as` rule's reconciler
withdraws only its own rule's pairs, so it leaves these standing, though no
shared handle row supports them. A trashed person is nobody's target
(`PartyManager.contact_people`), so its record lands on a new person. The ingest
verbs decide nothing: they land on the target they are given or mint one, and
refuse to move a link off a live party.

[PartyManager.ingest_contact](managers.py) asserts directory facts; Address and
Relationship keep their own identities and statements. Missing members withdraw
only that source. Canonical scopes retain unasserted rows and exclude trash and
wholly withdrawn rows. [Handle sources](managers.py) compose `Party.statements`
through the shared Handle, Party lock order; resolution owns the cross-party
winner and derived counters outside Party history.

Who an email address names is one verb,
[PartyManager.people_for_emails](managers.py): given addresses in a caller's
preference order, it answers the people they name, each with its basis (the
account with that address, the live person holding it, else, whenever no live
person holds it, the one person a current contact claim proposes it for),
comparing handles by their normalized value. A trashed person, an organization
and a merged-away row are nobody's answer, and a holder that is no live person
does not stop the claims: a person minted earlier for an organization's address
is found again by their claim. It
decides nothing: directory adoption, the connections import (which shares its
holding rule) and an importer in another addon each apply their own policy, and
only a `HELD` answer holds the address.

[PartyManager.ingest_organization](managers.py) lands a `ParsedOrganization`
from a directory stream the same way: it binds the link, mints an Organization
for an unbound link (matching an existing one is not decided yet), and states the
identity, the whole legal identity (legal name and domain together, or withdrawn
when the source carries neither), handles and addresses;
`organization_projection` is its comparison shape, over the stored form
`prepare_contact` gives organizations as it gives contacts. Neither verb rebinds
a link bound to a live party: binding lends the party to the directory's contact
readers, so a target must be of the verb's kind and, for such a link, its own.
Both verbs land
addresses through `AddressManager.land_addresses`. Employment has one verb,
[RelationshipManager.land_employment](managers.py): an employer is a tracked
Organization or a free-text name, a link may name several, and the link's edges
it no longer names lose its statement. Contact ingest and the social connections
import both call it.

A card's `PHOTO` is stored as the person's avatar through storage's idempotent
`File.objects.ingest_image` before the page transaction; a photo storage refuses
(not a raster image, or over `ANGEE_STORAGE_IMAGE_MAX_BYTES`) quarantines its
card while the rest of the page lands, and write-back refuses to send such an
avatar as a remote rejection. Only the person's own `avatar` is projected. A
handle carries the avatar its own network shows: live sessions report it as an
`AvatarReport` through `Handle.objects.fill_avatar`, which never creates a
handle. A party without a readable avatar of its own displays the avatar of its
strongest handle the viewer can read (`Party.objects.display_avatar`); that
fallback is derived for reading and never stored or written to a card. Avatars
live on storage's image drive, which every signed-in user reads; which avatar a
party or handle shows stays with that record's readers.

A handle that proves to be another's spelling folds through the shared
`MergeableMixin` into the contact point on the same platform. Handle owns missing
value carry and the directory identity lock order; the shared reference mover
preserves statement decisions and evidence. The alias remains as a historized
redirect, reserving its ingestion keys so later syncs resolve to the survivor.

Ordinary person edits assert normal statements; confirm/dismiss writes preferred
statements without rewriting the source or copying evidence. Person preferences
survive remote removal: confirmed handles can be republished; dismissal makes
removal stick. Revert restores local state; sync retains its remote preconditions.
Consumers declare domain aspects; losing statements stay inspectable, and normal
handle statements below the Party's review threshold remain pending review.

Review and Identity read through the Party, with independent Handle and evidence
gates. Once asserted, subjects leave active use only through trash;
retained statements protect their subjects and Handle evidence from deletion.
Circle and spaces memberships keep plain acceptance/rejection decisions.

Writers can share a Party's basic identity through `identity_reader` on the
canonical Party. Its Person or Organization row reads that grant through its
`party` link, while private fields, related subject records, handles and banking
keep their own gates. The existing `reader` relation includes private Party reads; use it only
when that wider access is intended. Both use the native record-access owner.
Adding an address or relationship to a party takes write on that party
(`create` on `parties/address` and `parties/relationship` in [permissions.zed](permissions.zed)):
the row changes the party's projection, which a two-way directory writes to its
card, so no read grant attaches anything.

Sharing a directory shares its contacts: `contact_reader`, granted on the
directory row through its share surface, and the directory's owner read the
people and organizations its live links name and the handles, addresses and
relationships those links state (`contact_read` in [permissions.zed](permissions.zed)).
Each statement arm walks from the row to the statements naming it by the
statement model's name (`addressstatement`, `relationshipstatement`, and
`partystatement`, which the handle member declares as its reverse name). A
tombstoned or unbound link, or a withdrawn or superseded claim, lends nothing; a
handle only another source asserted keeps its own gates; private fields keep
`read_private`; and the grant reads nothing of the directory, its integration,
streams, links or credential. Beyond the party's identity, a contact read
currently also reveals: the tags on the party and on its readable handles
(`tags/tag_assignment` reads through the record; whether a contact read should
show them is open with the architect); the person in people pickers when they
have an account (`auth/user#colleague`); the title, notes and dates of an
employment edge the directory states; and every source's statements on a
readable handle (the party's `statements` field).

Source admission follows RecordLinks and seeded Rule/Seed role membership.
Sources gain no private reads. Imports supply their
Integration and observed links; parties binds targets in the application transaction.

Identity matching writes normal RULE same_as statements on the ordered Party
pair, skipping current assertions and dismissals before each run's cap without
continuation state. A preferred no_value is the symmetric anti-link; preferred value
awaits a person's survivor choice. `survivor.merge` owns the changeset and redirect;
pointing owners union statements, retaining collisions as superseded. Where the
records disagree on a merge field, the merging person confirms the value kept
(the survivor's, or the chosen override), so no moved statement outranks it.
