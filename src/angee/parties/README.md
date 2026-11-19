# Directory synchronization

A Directory composes the [record-sync driver](../integrate/README.md). Its
`addressbooks` stream mirrors each discovered address book as a Folder: the
book's RecordLink targets the folder and is the container of that book's
`contacts` stream, whose partition is the book's href. The directory states the
folder's name through that link ([FolderManager.ingest_addressbook](managers.py)),
so a person's rename outranks a remote one. A book missing from the listing
pauses its contacts; once its absence is confirmed it retires its contacts
stream, whose contacts are tombstoned, while folders and parties stay. An empty
listing confirms nothing, and a refused discovery raises. `streams()` declares
from local state only. RecordLink owns
external identity and wire payloads. Party rows remain independent of any one
directory.

The [CardDAV backend](../parties_integrate_carddav/backend.py) compares the shared
[contact projection](backends.py); wire properties outside that projection stay
in RecordLink history and survive conditional writes.

[PartyManager.ingest_contact](managers.py) asserts directory facts; Address and
Relationship keep their own identities and statements. Missing members withdraw
only that source. Canonical scopes retain unasserted rows and exclude trash and
wholly withdrawn rows. [Handle sources](managers.py) compose `Party.statements`
through the shared Handle, Party lock order; resolution owns the cross-party
winner and derived counters outside Party history.

[PartyManager.ingest_organization](managers.py) lands a `ParsedOrganization`
from a directory stream the same way: it binds the link, mints an Organization
for an unbound link (matching an existing one is not decided yet), and states the
identity, the whole legal identity (legal name and domain together), handles and
addresses; `organization_projection` is its comparison shape. Both verbs land
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

Sharing a directory shares its contacts: `contact_reader`, granted on the
directory row through its share surface, and the directory's owner read the
people and organizations its live links name and the handles, addresses and
relationships those links state (`read_contacts` in [permissions.zed](permissions.zed)).
A handle reaches the directories asserting it through the reverse name its
statement member declares (`party_statements`). A tombstoned or unbound link, or a
withdrawn claim, lends nothing; a handle only another source asserted keeps its
own gates; private fields keep `read_private`; and the grant reads nothing of the
directory, its integration, streams, links or credential.

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
