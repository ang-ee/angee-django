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
winner and derived counters outside Party history. A handle's holder is that
winner's subject, so sources state links and never write `party`. A holder a row
carries with no statement behind it (a seeded row, an older write) is stated by
the `parties.stored_holder` rule after its resource load and by the
`resolve_stale_owners` repair, which runs once per deployment and again harmlessly;
until then the next resolution of that handle would clear it.

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

Source admission follows RecordLinks and seeded Rule/Seed role membership.
Sources gain no private reads. Imports supply their
Integration and observed links; parties binds targets in the application transaction.

Identity matching (`PartyManager.reconcile_same_as`) writes normal RULE same_as
statements on the ordered Party pair for every two live parties of one kind linked
to one address (`PartyManager.same_person_links`). A party is linked when it holds
a handle of that normalized value, or when a source that states its own record (a
person, a system of record, a directory, an import, a source's claim) names one for
it in a standing statement, a claim below review confidence included. Any other
rule or extraction, parties' own suggesters among them (a shared name, a domain, a
twin spelling, a signature), links nobody until it is named, so a shared name alone
proposes no one. Only an address that names one account links: an email address, a
phone number, or a handle with a platform id, never a name-only handle. A person is
never paired with an organization, nor two accounts' own persons with each other,
and an account's contested claim to an address another account controls links
nothing. An address more than `PartyManager.SHARED_ADDRESS_LIMIT` parties are
linked to (a switchboard, a family line) is shared and pairs nobody, as is one the
shared-sender providers name. A pair needs two sources: two parties whose only
links to the address one source states are that source's distinct records (two
cards of one address book), so they are not proposed. A holding counts as the
source of the statement that decides it; a source's claim and a holder found on its
row name no single source, so each is a source of its own. The job finds the
candidate addresses in SQL and reads only their links. Pairs follow from the
model across owners too: two users' private contacts linked to one address are
proposed to whoever can read both. The job
skips current assertions and dismissals before each run's cap without
continuation state, and withdraws its statements no link supports any more. An
importer that lands its own party and claim proposes the same pair at once with
`Party.objects.propose_same_person(left, right, evidence=handles)`, which states
the same rule's statement, names no caller and never merges. A
preferred no_value is the symmetric anti-link; preferred value
awaits a person's survivor choice. `survivor.merge` owns the changeset and redirect;
pointing owners union statements, retaining collisions as superseded. Where the
records disagree on a merge field, the merging person confirms the value kept
(the survivor's, or the chosen override), so no moved statement outranks it.
