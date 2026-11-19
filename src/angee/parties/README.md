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
connect mutation calls outside any transaction (it refuses to run inside one):
it checks that its owner may create a directory, builds the credential and the
directory unsaved, has the backend admit the credential and test the connection
outside any transaction, refuses with the backend's own message before anything
is saved, then atomically creates the directory and a credential of its own,
named after the backend, the display name and the directory's id, so no
connection or reconnect reuses another's, connects the one with the other and
claims the address the account signs in as (`DirectoryBackend.account_address`)
as the owner's own handle. The first sync is requested once that commits, when
the directory is ready for work; a backend whose connection discovery is pending
gets its first sync when discovery completes. A failure to queue it is logged.
Each vendor's row is a master resource adopted by slug, and connecting before it
is loaded is a refusal that says to load resources; any other configuration
error stays itself.
The [CardDAV backend](../parties_integrate_carddav/backend.py) syncs two-way and
builds its own records: a card's `UID` is its key and its href rides in the
link's metadata, and wire properties outside the projection stay in RecordLink
history and survive conditional writes. Its configuration is a typed config
model: every save of the directory validates the keys it knows and drops the
others, and a book's stream reads only the policy keys it knows.

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

A consumer addon fills its own person columns from a pull-only directory's
source fields in code, with a [directory mapping](backends.py)
(`DirectoryMapping`): its donor of `parties.Person` declares the columns and the
statement group that owns them, whose precedence decides against the person's
own values, and its mapping names the backend, the source fields and a version,
and is registered under its key in `ANGEE_DIRECTORY_MAPPING_CLASSES` from its
`autoconfig.py`. The backend asks its source for the fields its mappings name
(`DirectoryBackend.mapped_fields`) and passes their values to `observe`; each
mapping's `project` keeps what may enter the system, and only those projections
are kept, under the payload's `mappings` key, which is also part of the
comparison, so a changed mapped value applies. After the contact's ingest, in
its savepoint, each mapping states its values on the landed person, minted or
adopted, through the contact's link, so withdrawing the link withdraws them.
Each mapping is one part of the contacts stream's mapping: adding, removing,
exchanging or re-versioning one replays every contact once. A mapping declares
the person statement groups it states (`aspects`); on every pull-only record the
base withdraws what the link states in a person group that neither the ingest
(`PartyManager.contact_aspects`) nor a registered mapping states, so a mapping
removed from the registry has its statements withdrawn on its replay, and nothing
else on the link. A mapping that cannot read or state one record's value raises
`ValueError` (or `TypeError`): that record alone is refused (`mapping_refused`,
naming the mapping, never the value), the rest of its page lands, and the record
applies once its source changes. Without a mapping a backend asks for, keeps and
compares exactly what it did before. A mapping of a backend that writes back, of
no registered backend, or declaring no group or a group the person has not, fails
the system check.

One function decides a contact's target (`DirectoryBackend._contact_target`),
once, under the page's and the contact's locks: the person its own link keeps,
then the person a replaced link hands over, then, on a pull-only stream, the
first candidate the binding owner admits: the person its own link named when it
returns from a tombstone, a person a replaced link that hands nothing over names,
then an existing person one of its emails names (the preferred email first). Only
a bound, observing link of the contact's own stream keeps or hands over its
person (`PartyManager.holding_links`); a two-way stream's own link keeps it
through a tombstone, since such a stream adopts nobody. A page locks, in one call
and in the identity order, every spelling of its contacts' addresses and every
handle stated on a party its links (its tombstones' included) or those addresses
name, or on the person of an account one of them signs in with
(`PartyManager.account_people`), then those parties,
and only then reads who its addresses name, once
(`PartyManager.lookup_emails`: `people_for_emails`, and which addresses a person
account signs in with, `UserManager.sign_in_addresses`), so no record locks a
handle after a person (`DirectoryBackend.prepare_page`). A merge of parties locks
every participant's handles, then the participants, in one call
(`Party.lock_merge_records`), and an account adopting a person locks the person's
party row with it, so both wait for a page. The ingest and the target
decision read that lookup; an address a record lands or withdraws (its own link
leaving a person, or a tombstone: `PartyManager.withdraw_contact`) is read again
by the next record that asks for it. Adoption is an access change, so the binding owner
admits it: `PartyManager.adoption_blocker` refuses unless the stream is pull-only,
the person, loaded under the locks, is live and no account's own, no address the
contact carries is a person account's sign-in address in any spelling, whether
or not the account has a person, the directory's owner holds `adopt` on them
(their owner, or an editor of their party or of the person, with no platform-admin arm, since every directory's
owner is a platform admin; checked for that user though sync runs as system
work), no other contact of the directory names them, a tombstoned one included,
and no live link of a stream that writes back names them. The page's lookup keeps
each verdict, so a person is admitted once per record. `ingest_contact` asks it
for every person new to its link. What is enforced, and by whom: the directory
base's target decision and its `apply_record`, and the ingest verbs, which every
caller of them passes through. Integrate's own link writers bind a link to any
record without asking (`promote` from an apply result's `target`, `revalidate`,
`observe(target=...)`), so a vendor backend that overrides `apply_record` and
returns a target of its own choosing is trusted code; moving the admission to the
link's own binding owner is open with the architect. Otherwise the contact mints
its own person; a two-way directory never adopts. The handle an adoption found
the person by is the evidence of the directory's identity statement on them,
restated on every later record, and the contact's spelling of that address lands
on the same handle row. Known gap, open with the architect: a source states whole
aspects, so on an adopted person a field no statement holds (a value written as
system work, or stated by a lower-ranked source) takes the source's value, an
empty one included; a contact without notes, photo or birthday clears the
person's.

What a pull-only contact may state about an address depends on whom the lookup
finds by it (`PartyManager._contested_emails`): an address it answers with a
person other than the contact's own, who does not hold it too, is someone else's,
and so is an address a person account signs in with, whether or not the account
has a person, so the contact states it as a claim for review (below the confidence at which a
handle statement applies) and leaves the handle's display fields alone; an
address nobody holds, or the contact's own person holds, is stated in full. A
lapsed holder is still the holder: a trashed person whose applied statement of
the address is current (`PartyManager.lapsed_holders`) gets it back on restore,
so the contact only claims it, and a claim names nobody to adopt by it. The
lookup carries them beside `people_for_emails`' answers, which stay as they are.
A claim stays a claim until the contact's person holds the address through another
statement or a person confirms the claim: a holder that lapses (trashed, or its
statement withdrawn) and returns finds its address still its own. A person's
stance that wins a handle moves the address: confirming a claim, taking control
of an address (`HandleManager.claim_control`) and a confirmation a history revert
restores all end in the handle's resolution (`Party.apply_handle`), where its
author needs write on every other holder of the address as the page lookup reads
them (`PartyManager.holding_blocker`): each live party any spelling of it
resolves to, a lapsed holder, and an account's person when it is an account's
sign-in address. An account takes its own sign-in address whoever holds it, and
nobody may while the account has no person. The refusal is one message whoever
holds the address. A claim lends the directory's contact
readers nothing of the holder's handle (`stating_directory` reads current claims
at `Party.REVIEW_CONFIDENCE` or above). A refused or colliding contact therefore
never moves an address, and what is attributed to it, away from its holder. A
two-way directory (CardDAV) still
states every address in full: its card projects only applied handle statements,
so a claim would drop the address from the card and write that back to the
server, and what a two-way card publishes about a person other sources state is
open with the architect.

Each person the contact's addresses name instead (a candidate an adoption limit
refused, or a person a collision leaves holding the address) and each other
person a replaced record was linked to is proposed as the landed person's same
person by parties' `directory_match` rule
(`PartyManager.propose_directory_match`), citing the contact's link and the
handle or the replaced link, and joins the same-as review. A pair kept separate
is not proposed again; merging the two withdraws the proposal. The survivor of a
merge takes every link of the merged person, so a person who writes both may
merge a pair an adoption limit refused (the deliberate override of `adopt`), but
the merge still refuses to bring together two contacts of one pull-only
directory, a pull-only directory's contact and an account's own person, or such a
contact and a person a stream that writes back names
(`PartyManager.directory_merge_blocker`, from `Party.validate_merge`; default
pending ruling O16). The `same_as`
rule's reconciler withdraws only its own rule's pairs, so it leaves these
standing, though no shared handle row supports them. A trashed person is
nobody's target (`PartyManager.contact_people`), so its record lands on a new
person or adopts one; the link first withdraws what it stated on the trashed
person, never handing it to another link of its source that names them too, and
the proposals pairing the trashed person with the new one, or with
whom the contact's addresses name, are withdrawn with it. A record returning from
a tombstone whose person is refused again lands on another person, and that
person stays proposed as its same person. The ingest verbs
decide nothing: they land on the target they are given (admitted as above) or
mint one, and refuse to move a link off a live party.

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
is found again by their claim. An account names only its existing person: the
lookup writes nothing. It
decides nothing: directory adoption, the connections import (which shares its
holding rule) and an importer in another addon each apply their own policy, and
only a `HELD` answer holds the address.

[PartyManager.ingest_organization](managers.py) lands a `ParsedOrganization`
from a directory stream the same way: it binds the link, mints an Organization
for an unbound link and, until matching an existing one is ruled, refuses an
existing organization new to its link, under the record's locks; it states the
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
tombstoned or unbound link, a withdrawn or superseded claim, or a claim for review
lends nothing; a
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
