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
`resolve_stale_owners` repair, which every provision runs (`PartiesConfig.compose_provision`)
and which is idempotent; until then the next resolution of that handle would clear
it. The rule is the last kind the handle aspect ranks: a person's, a system of
record's, a directory's or an import's statement outranks it, kinds the aspect does
not list rank below it. So the repair moves no address and clears no
confirmation: it leaves a confirmed holder (only a person or a seed states the
preferred link that keeps the flag) and a holder another party's applied statement
contests for a person, and reports both. It leaves a trashed holder's address
alone (resolution ignores a trashed party, so stating or re-resolving it would
clear the row) and states it after a restore; a merged-away holder is stated for
its survivor. One run writes at most `HandleManager.REPAIR_LIMIT` (500) holders;
past the cap the command, and so the provision log, ends with "Stopped at the cap
of N holder(s); M more await the next provision or resolve_stale_owners run", and
the next run continues (`--limit` sets another cap). A seed that moves or clears a
holder withdraws the statement its earlier load made for the old one.

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
it in a standing statement, a claim below review confidence included, whatever
its evidence names (the handle, or the source's record link); a claim re-stated
with more evidence is the same link and changes no pair. Any other
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
continuation state, and withdraws its statements no link supports any more, but
not one stated after its run began. An importer that lands its own party and claim
proposes the same pair with
`Party.objects.propose_same_person_as_system(left, right, evidence=handles, reason=...)`.
It is best-effort enrichment of the importer's write: it only registers the
proposal, which runs after the importer commits, in its own short transaction, so
a rolled-back page proposes nothing and a page never waits on a pair's locks. It
checks that each cited handle links one of the two and that they share a cited
address, states the same rule's statement, names no caller and never merges; the
job heals a proposal it missed. A pair locks both parties in one identity pass
(`Party.lock_subjects`, the provenance pair-lock hook), Handles before Parties, as
a merge does. A
preferred no_value is the symmetric anti-link; preferred value
awaits a person's survivor choice. `survivor.merge` owns the changeset and redirect;
pointing owners union statements, retaining collisions as superseded. Where the
records disagree on a merge field, the merging person confirms the value kept
(the survivor's, or the chosen override), so no moved statement outranks it.

Nothing merges unattended. A person may also merge in a batch: confirmed sets of
two to ten records each (`PartyManager.merge_duplicates`, the `merge_duplicates`
mutation; at most 50 sets), either the certain pairs the Review page lists
(`certain_same_as_pairs`) or a selection from the People or Organizations list.
There, one selected record opens "Merge into…" to choose the other, two open
their compare page, and three or more are one explicit set, the whole
selection, which the dialog lists before anything merges. Only an active person account that writes every record may (the party's
`batch_merge` permission, `write & person->active_member`); a service or agent
account merges nothing, and no agent tool offers it. A set never holds an account's
own person (merged by hand, on the compare page), records of two kinds, a trashed
or merged record, or two records a person kept apart; each is refused for that set
with its reason, and a set merges exactly as the person confirmed it or not at all.
A pair is certain when both are people, neither is an account's own person,
exactly the two are linked to an address the pair cites, and both bear a real
name, none of the pair's cited addresses (a record named after its e-mail has no
name of its own), equal to the other's but for case, accents and hyphens. In each
set the oldest record survives and the others fold into it in key order, under one
lock pass. A batch records no value as the person's choice: each record's
statements move to the survivor as their sources' own and the provenance ranking
decides what shows; a field the survivor leaves empty takes a merged record's
value as a plain value no one states (`Party.merge_values`). The dialog shows, for
each set that merges, the fields its records disagree on and the value that will
show, leaving out a field the person's `read__<field>` gate hides on any record.
Each set that may merge has its own Include toggle, off until the person ticks it
when the set carries a warning (its records disagree, or one no longer matches its
group) or has changed from the state the person saw or ticked it in (a refreshed
preview, a refused set that became mergeable), and the merge sends only the
included sets, each with the digest of what
the dialog showed of it: a set whose shown names, addresses or sources changed
since is refused under the lock and asks the person to look again; a
person who wants to choose merges that pair on the compare page instead. A merge,
batch or not, reverts from its changeset. A person's `anniversary` is a merge
choice like their birthday; a party's `avatar` is not one yet. Merging a person
with an organization is refused by every merge.
