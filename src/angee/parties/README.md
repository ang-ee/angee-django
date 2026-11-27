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
model. It is validated strictly where a person edits it, `Directory.objects.connect`,
the one verb each vendor's connect mutation hands its configuration to (the
directory's resource and form never edit it): a setting the model does not know
is refused there. A save that edits nothing keeps what is stored, unknown keys
included at every level; a known setting written with an invalid value is
refused. A reader ignores what it does not know: a book's stream reads only the
policy keys it knows.

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

A party's `created_at` is the earliest time its provenance states. A contact
carries the time its source says the record was created (`ParsedContact.created_at`,
kept in the payload beside the compared projection, so it is never a change to
apply); a person the contact makes takes it, and a contact landing on a person that
exists moves the person's earlier when it is earlier, never later, as a merge moves
its survivor's to the earliest of what it folds in (`Party.backdate_creation`, through
the one rule `PartyManager.backdate_creation`). A contact re-read unchanged, which
integrate settles without applying it, moves its person too
(`DirectoryBackend.on_revalidated`, from the payload just stored; a merged record's
link names its survivor). So on an instance whose contacts were read before they
carried their time, people move as the directory reads them again: the daily sweep
re-reads every contact, and a Resync or a change at the source reads one sooner.
Every provision runs `backdate_parties_from_sources`
(`PartyManager.backdate_from_sources`), the catch-up for payloads already holding a
time: each party takes the earliest of its own and its directory records' stored
times, and a record whose payload states none is counted and left for its next read
from the source. Its line gives those counts and, when it moved any, the earliest
and latest new time: no name or id.

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
the person statement groups it states (`aspects`), never one the ingest states
(`Person.contact_aspects()`, read from parties' own declarations). A person's
`languages` (ISO 639 codes, most preferred first, `LanguageCodesField`; one
language elsewhere is a `LanguageCodeField`, and `normalize_language_code` is the
one spelling rule both use) and
`occupation` (free text, apart from an employment's title) are parties' own
groups, one each, that no contact carries (`Person.MAPPED_ASPECTS`): a mapping
states them, and a person's own statement outranks it. A source field that
belongs in a group the ingest states (a house's name for a person is the
contact's `nickname`) is no statement of the mapping's: the mapping refines the
neutral contact from its projection before the base keeps it
(`DirectoryMapping.refine`, the fields it declares as `refines`, among
`REFINABLE_FIELDS`: the name parts, the dates, the employment, and the
preferred flag of an email or phone the source sent, never its value, label or
order, nor the display name, notes, addresses or photo), so the refined field
is compared, versioned and stated by the ingest like the source's own, a
person's own value still wins by rank, and removing the mapping replays the
contact, whose ingest then states the source's own value. The preferred mark of
the channel a contact chose is such a refinement when the contact brings that
address, so the ingest writes it once. A mapping may also write through
other owners' verbs in its `apply_record`, declaring nothing for them: tags
(`TagAssignment.objects.attach`) and the preferred mark of a handle the contact
does not bring, a messenger's (`Handle.objects.upsert(..., is_preferred=True)`).
Neither is taken back when the mapping or the link goes. A mapping that writes
a person's text into their folder of the people vault reads what it last wrote
from its link (`DirectoryMapping.applied`, the projection the link last applied)
and hands it to knowledge's page writer, which never writes over a person's
edit; the page stays when the mapping goes. On every
pull-only record the base first withdraws what the link states in a person group
that neither the ingest nor a registered mapping states, so a mapping removed
from the registry has its statements withdrawn on its replay, and nothing else on
the link; a mapping that then states such a group fails the page. A mapping that
cannot read or state one record's value raises `ValueError`: that record alone
is refused (`mapping_refused`, naming the mapping, never the value, and logged
with the mapping's and the record's key), and the rest of its page lands. The
stream's rescan retries the record on its backoff (a minute, doubling, at most a
day; an operator's retry makes it due at once), so a mapping fixed in code applies
it without a source change, and a changed source applies it on its next page. Any
other error a mapping raises is a bug: it fails the page with its traceback.
Without a mapping a backend asks for, keeps and compares exactly what it did
before. A mapping of a backend that writes back, of no registered backend,
declaring a group the person has not or a group the ingest states, or refining
a field that is not open to it or that another mapping of its backend refines,
fails the system check.

Each source creates its own people, and a merge joins them. One function
decides a contact's target (`DirectoryBackend._contact_target`), once, under the
page's and the contact's locks: the person its own link keeps, through a
tombstone too; then the person a replaced record hands over (a bound, observing
link of the contact's own stream, `PartyManager.holding_links`); otherwise a new
person, which only claims an address someone else is linked to and is proposed
as their same person (below), so a person who writes both joins them by a merge.
The ingest verb refuses any other person a caller hands it
(`PartyManager.ingest_contact`). A page locks, in one call and in the identity
order, every spelling or alias of its contacts' addresses and every handle stated on a
party its links (its tombstones' included) or those addresses name, or on the
person of an account one of them signs in with (`PartyManager.account_people`),
then those parties, and only then reads who its addresses name, once
(`PartyManager.lookup_emails`: `people_for_emails`, and which addresses a person
account signs in with, `UserManager.sign_in_addresses`, compared by iam's key and
by the handle's normalized one), so no record locks a handle after a person
(`DirectoryBackend.prepare_page`): a record proposes the people its addresses
name, and a proposal locks both people. A merge of parties locks every
participant's handles, then the participants, in one call
(`Party.lock_merge_records`), and an account adopting a person locks the person's
party row with it, so both wait for a page. The ingest reads that lookup; an
address a record lands or withdraws (its own link leaving a person, or a
tombstone: `PartyManager.withdraw_contact`) is read again by the next record
that asks for it.

Adoption, binding a contact to an existing person, is an option of a pull-only
directory, off by default: its configuration's `adopt_existing_people`
(`DirectoryConfig`, the directory base's config model, which a backend's own
model extends), validated on every save and strictly by
`Directory.objects.connect`. A directory that writes back refuses it, so a
two-way directory (CardDAV) never adopts. `DirectoryBackend.adopts_existing_people`
is the one predicate: the target decision is the one place that asks it before
entering adoption, and the ingest verb asks it only to refuse a person new to
the link while it is off. With it on, a contact that no link of its own or of a
replaced record holds a person for lands on the first candidate the binding
owner admits: the person its own link named when it returns from a tombstone, a
person a replaced link that hands nothing over names, then an existing person
one of its emails names (the preferred email first), read from the page's
lookup. Adoption is an access change, so the binding owner admits it:
`PartyManager.adoption_blocker` refuses unless the stream is pull-only, the
person, loaded under the locks, is live and no account's own, no address the
contact carries is a person account's sign-in address in any spelling or alias, whether
or not the account has a person, the directory's owner holds `adopt` on them
(their owner, or an editor of their party or of the person, with no
platform-admin arm, since every directory's owner is a platform admin; checked
for that user though sync runs as system work), no other contact of the
directory names them, a tombstoned one included, and no live link of a stream
that writes back names them. The page's lookup keeps each verdict, so a person
is admitted once per record. `ingest_contact` asks it for every person new to
its link. What is enforced, and by whom: the directory base's target decision
and its `apply_record`, and the ingest verbs, which every caller of them passes
through. Integrate's own link writers bind a link to any record without asking
(`promote` from an apply result's `target`, `revalidate`, `observe(target=...)`),
so a vendor backend that overrides `apply_record` and returns a target of its
own choosing is trusted code; moving the admission to the link's own binding
owner is open with the architect. Otherwise the contact mints its own person.
The handle an adoption found the person by is the evidence of the directory's
identity statement on them, restated on every later record, and the contact's
spelling or alias of that address lands on the same handle row (for an alias, the
person-matching key picks it, one of the two sites still open for a ruling); a link keeps its adopted
person if the option is turned off. Known limit of the option, open with the
architect and pinned by a strict expected failure in the directory tests: a
source states whole aspects, so on an adopted person a field no statement holds
(a value written as system work, or stated by a lower-ranked source) takes the
source's value, an empty one included; a contact without notes, photo or
birthday clears the person's.

What a pull-only contact may state about an address, whether or not its
directory adopts, depends on whom the lookup finds by it
(`PartyManager._contested_addresses`): an address it answers with a
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
them (`PartyManager.holding_blocker`): each live party any spelling or alias of it
resolves to, a lapsed holder, and an account's person when it is an account's
sign-in address. An account takes its own sign-in address whoever holds it, and
nobody may while the account has no person. The refusal is one message whoever
holds the address. Taking control itself never refuses: signing in (OIDC),
connecting a directory and attributing a user's own message all call
`claim_control`, which, where the rule would refuse, states the address for the
user's person as a claim for review at `Party.CLAIM_CONFIDENCE` and leaves control
and the holding as they are. A claim lends the directory's contact
readers nothing of the holder's handle (`stating_directory` reads current claims
at `Party.REVIEW_CONFIDENCE` or above). A contact therefore never moves an
address, and what is attributed to it, away from anyone it names. A
two-way directory (CardDAV) still
states every address in full: its card projects only applied handle statements,
so a claim would drop the address from the card and write that back to the
server, and what a two-way card publishes about a person other sources state is
open with the architect.

Each person the contact's addresses name instead (whoever holds or claims one,
an adoption candidate a limit refused included) is proposed as the landed
person's same person through the one same-person verb,
`Party.objects.propose_same_person_as_system`, citing the handle that names them,
once the page commits: the pair is the one the pairing job would find, two
parties linked to one address. What no address links is proposed by parties'
`directory_match` rule (`PartyManager.propose_directory_match`), citing the
contact's link and the replaced link: each other person a replaced record was
linked to (a merge at the source, the directory base's `apply_record`), and an
account whose sign-in address the contact states while no handle of theirs
carries it. Both join the same-as review. A pair kept separate is not proposed
again; merging the two withdraws the directory's proposal. The `same_as` rule's
reconciler withdraws only its own rule's pairs, so it leaves the directory's
standing, and the rule's earlier proposals of address pairs stay reviewable.

The survivor of a merge takes every link of the merged person, so a person who
writes both may merge any pair, an account's own person or a person a two-way
card names included, except two live contacts of one pull-only directory: the
source keeps them separate, and the directory's statements would overwrite each
other on every sync. A tombstoned link does not count, so once the source has
merged or removed one of them, the two merge (`PartyManager.directory_merge_blocker`,
from `Party.validate_merge`). A trashed person is
nobody's target (`PartyManager.contact_people`), so its record lands on a new
person or adopts one; the link first withdraws what it stated on the trashed
person, never handing it to another link of its source that names them too, and
the proposals pairing the trashed person with the new one, or with
whom the contact's addresses name, are withdrawn with it. With adoption on, a
record returning from a tombstone whose person is refused again lands on another
person, and that person stays proposed as its same person. The ingest verbs
decide nothing: they land on the target they are given (admitted as above) or
mint one, and refuse to move a link off a live party it holds.

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

Who an email address names is one verb,
[PartyManager.people_for_emails](managers.py): given addresses in a caller's
preference order, it answers the people they name, each with its basis (the
account with that address, the live person holding it, else, whenever no live
person holds it, the one person a current contact claim proposes it for),
comparing handles by their person-matching key. A trashed person, an organization
and a merged-away row are nobody's answer, and a holder that is no live person
does not stop the claims: a person minted earlier for an organization's address
is found again by their claim. An account names only its existing person: the
lookup writes nothing. It
decides nothing: its one caller is the page lookup (`PartyManager.lookup_emails`),
which a directory's claim rule, its adoption when on, and the holding rule read;
the connections import shares its holding rule. Only a `HELD` answer holds the
address.

An integration that is no directory, whose records carry a contact (a name, an
email address, a phone number), makes its person through one verb,
[PartyManager.claimant_as_system](managers.py), under the same rule: each
source creates its own people. The caller hands over the record that carried
the contact (`evidence`). Which records are its own is the caller's rule, and
`own` carries it: a queryset of them, the record links of its integration.
The email decides, and a contact without one is decided by its number. The
person found again is one whose standing claim on a handle of that address has
a row of `own` behind it, never one a column names: a person a record of the
source merely points at (one it adopted from someone else) is not its own.
Another spelling or alias is the same address, a trashed person is nobody's answer, a
merged one is found as its survivor, and of several the earliest. Nobody else's
person is taken, whoever holds or claims the address, an account's own person
included; the verb makes a new person instead, named as given and created by
the owner the caller names. A contact with neither an address nor a number finds
nobody again. An email that cannot be an address at all is no address, and a
number the handle owner reads nothing in, or refuses, is no number: each is
left out and makes no handle, so two contacts that gave the same non-address
are never one person for it. What can be an email address is the handle owner's
one predicate, `Handle.is_email_address`: exactly one `@`, a non-empty name
part in any script (`jörg@`, `peña@`) with no whitespace, control or format
character (a zero-width space, a direction mark), a domain that passes the
domain rule of Django's `EmailValidator` (an internationalised one as typed or
punycoded), and no longer than an address may be or the handle's value holds.
An email handle is one address whatever its letter case or domain spelling:
the platform matches addresses case-insensitively, the part before the `@` as
well as the domain, though a mail server may tell `Ada@` and `ada@` apart.
`upsert` and `find_identities` land any such spelling on the one live handle,
which keeps the spelling first written as its `value`, what readers show and a
message to it is addressed to unless its writer was handed another spelling
(messaging's `Recipient.address`, which mail goes to); its spelling key (`Handle.spelling_key_of`) is what a live email
handle is unique by (`uq_handle_spelling_key`). `Handle.is_spelling_of(value,
merged=True)` also answers for every handle merged into it, in one read, as a
fold re-points each redirect at the survivor; `HandleManager.find_identity_owners`
returns the row owning each identity before its merge redirect, which
`find_identities` resolves to the survivor. One address, by that key, is
every letter case of it (lower-cased, not case-folded: `Straße@` and `STRASSE@`
are two) and every spelling of its domain that the email backend sends as one
domain (IDNA 2003, as Django's `punycode` writes it): a punycode label and its
Unicode, NFD and NFC, full-width letters and their ASCII, `ß` and `ss`. A label
the standard library's `idna` codec cannot read or write stays as typed, so
`xn--strae-oqa` (IDNA 2008's spelling of `straße`) is a domain of its own. A
value with no `@` is no address and has no spelling key. A save that would keep a
second live handle of an address is refused on `value` before it writes
(`ValueOwned`, `Handle.value_owner`). A provider's alias (a Gmail dot or
plus-tag) is a handle of its own, grouped with the others by the person-matching
key (`Handle.normalize_value`, stored as `normalized_value`) wherever a person is
looked up; that key still picks the handle an alias lands on at two sites open
for a ruling, a directory adoption's through-handle and a claimant's email handle
(`PartyManager.claimant_as_system`). On other platforms, a spelling is a
handle, as before. That is
one rule, `Handle.normalize_email_domain`, for whoever compares a domain. The
organization-domain suggestion (`PartyManager.suggest_for`) reads it on both
sides, but `Organization.domain` is stored as typed, so it first finds
organizations whose domain is the handle's in the rule's spelling, its punycode,
or the address's own spelling, ASCII letter case aside: from `ada@bücher.example`
or `ada@xn--bcher-kva.example` it finds `bücher.example` and
`xn--bcher-kva.example`, and from `ada@straße.example` it finds `straße.example`
and `strasse.example`. It does not find an organization typed in another
spelling the rule joins (`straße.example` from `ada@strasse.example`, an NFD or
full-width spelling from the plain one), since organizations store no domain
key. A handle stores
the keys its rule gave when it was last saved (`spelling_key`,
`normalized_value`) and, beside them, the version the rule had then
(`normalized_version`; 0 for a row stored before the rule had one). The
handle's save is the one writer of the keys and writes them together. The rules
`Handle` owns (`value_owner`, `is_spelling_of`, `validate_merge`) compute a row's
own keys from its value, so a row the key repair has not reached decides as it
will; `PartyManager`'s readers of the person-matching key (the twins
`suggest_for` proposes, the holders read, `_land_handles`, the same-person links)
read the stored column, which is current once provision has run. Changing
what `Handle.spelling_key_of`, `Handle.normalize_value` or
`Handle.normalize_email_domain` returns means bumping
`Handle.NORMALIZATION_VERSION`; a test pins the rule's outputs for a fixed
sample beside the number, so an output that changes under one number fails.
The `renormalize_handles` repair (`HandleManager.renormalize_handles`), which
every provision runs (`PartiesConfig.compose_provision`), selects the handles
of every platform whose version is not the rule's, by that column's index, and
saves each again through the handle's own save, under its row lock: an earlier
version, 0, or a later one that newer code wrote before a rollback. A row
whose keys are already what the rule gives has them written again unchanged
beside its version, and leaves the selection. With no row behind, the repair is
that one indexed query. A run takes the whole backlog, because a row left with a
stale key is one the lookups that read keys miss, and a writer that misses it
makes a second row. It saves in batches of `HandleManager.REPAIR_LIMIT` (500)
rows, each in its own transaction, so an interrupted provision keeps what it
saved and the next one resumes. Every batch starts after the last key the run
selected, so a run selects no row twice and ends: a row deleted or already
saved by another process is passed, and a row whose save raises is rolled
back alone, logged, counted ("The keys of N handle(s) could not be saved;
...") and left for the next run while the rest are saved. A live email handle
that spells the address of another live one (two rows an earlier rule kept
apart) is deferred from its batch and, after the batch commits, folded in a
transaction of its own: the two rows are locked in the identity order, the
older one (`_age`) survives and the other is folded into it, kept as a
redirect ("Folded N handle(s) into the older handle of the same address; each
is kept as a redirect."). What refers to the folded row moves or stays by its
merge policy: a contact's statements (with their decisions and evidence),
calendar attendees and reactions move to the survivor, while a message keeps
the handle it was sent from or to, so a held message keeps its spelling; where
two contacts held the two spellings, one now holds the address and the
same-person review proposes the two. A fold the merge refuses (a seeded row, a
row under an unfinished workflow run) is logged once per run without a
traceback, naming both handles and why ("N handle(s) spell an address another
handle owns and could not be folded; the log names each and why."): the row
stays live and unkeyed, its exact value still finds it, every save that would
key it is refused, and the next provision tries again.
`renormalize_handles --limit N` saves a share instead, and only then ends
with "Stopped at the cap of N handle(s); M more await the next provision or
renormalize_handles run". Nothing is stated in full:
each handle the person does not hold is recorded as a claim for review
(`propose_claimed_handle_as_system`)
with the record behind it, which decides nothing and moves nothing. A claim
gains one evidence reference per record that carried the address, deduplicated,
and that is how the next record finds the person. A person found again has
nothing new stated of them and carries a handle only when they hold it or a
record of the source stands behind their claim on it; a new person's number is
known or made. The rows of the address and of the number, their parties and
their statements are locked in one `lock_identity_rows` call before any claim
is read, and which claims are the source's own is then one read of its records
by key, in batches within the database's parameter budget, however many
records stand behind a claim and however many parties claim the address. The
verb proposes no pair, and no caller needs to: a claim links its person to the
address, so identity matching (below) proposes the claimant and whoever else is
linked to it.

Which claims still stand is one scope,
[PartyStatementQuerySet.standing](managers.py), over handle statements the
caller narrows by handle or by party: each party's strongest current stance on
a handle, where it asserts a value. A stance its source withdrew or a merge
superseded makes nothing stand, and a person's dismissal outranks every claim.
`people_for_emails` and the identity lens of the party graph read it, and a
caller that asks only who still claims what reads no evidence.
`standing_claims` adds the records behind each claim, as its evidence names
them. When two parties merge, the claims one source made for each collapse into
one and the superseded one's records stay behind it, so two sources' claimants
of one address, once merged, are each found as the survivor. A caller's own
rule about which of its records may name a claimed address composes that read,
and compares the references it returns with its own by the record each names
(`EvidenceReference.named_record`: the label without case and the public id,
the pointer aside), as the verb does.

[PartyManager.ingest_organization](managers.py) lands a `ParsedOrganization`
from a directory stream the same way: it binds the link, mints an Organization
for an unbound link and, until matching an existing one is ruled, refuses an
existing organization new to its link, under the record's locks; it states the
identity, the whole legal identity (legal name and domain together, or withdrawn
when the source carries neither), handles and addresses;
`organization_projection` is its comparison shape, over the stored form
`prepare_contact` gives organizations as it gives contacts. Neither verb moves a
link off a live party it holds: binding lends the party to the directory's
contact readers, so a target must be of the verb's kind and, for such a link,
its own. A contact link keeps its person through a tombstone unless its
directory adopts: then its person is admitted again, and a refused one leaves it
on another person. An organization link keeps its own organization through a
tombstone, since no rule admits one again.
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

A handle that proves to be another's alias, or one address another rule kept
apart, folds through the shared `MergeableMixin` into the contact point on the
same platform. Handle owns missing
value carry and the directory identity lock order; the shared reference mover
preserves statement decisions and evidence. The alias remains as a historized
redirect, reserving its ingestion keys so later syncs resolve to the survivor.
Every later message to a merged handle's address goes to the kept one, so a
person's merge (`merge_records`) admits only another spelling or alias of the
kept address, the same platform and person-matching key as the rule computes it
from each value (never the stored column, which the key repair may not have
reached; a number the phone rule reads only as its digits, `Handle.is_fallback_key`,
is no one address), unless that person is a contact
administrator: an effective member of `parties/role:contact_admin`, as platform
administrators are. Anyone else is refused and merges the contacts instead,
which says two addresses reach one person and moves no address. Messages already
written keep the handle they were written to. A system fold (`reidentify`
folding an `@lid` into its number) merges any two handles on one platform. The
merge writes the redirect before it resolves both handles' parties, so a contact
administrator, who writes a handle only through the party it reaches, completes
it. That merge leaves the merged handle on no contact, so the administrator
cannot revert it: a platform administrator can, and so can the handles' creator
when the contact is also theirs to write.

A handle's ingestion keys, `platform`, `value` and `external_id`, are written
once (`Handle.immutable_fields`, through the foundation's `ImmutableFieldsMixin`).
A message reads its addresses from its handles when it is sent, so a field write
of one would send messages already written, a held one included, somewhere
else. Whoever holds `write` on a handle (its creator, a contact manager through
its party, an administrator) changes its display fields, and a save that changes
a key is refused with the foundation's write-once refusal, whoever saves it,
system work included. Only the handle owner's verbs change a key, each holding
the allowance for its own save: `HandleManager.upsert` rewrites `value` when a
source's `external_id` matched and the account behind it moved, and
`HandleManager.reidentify` rewrites `value` (a WhatsApp `@lid` resolved to its
number) or folds the handle into the row that owns the new value; another
letter case or domain spelling of an email address reported behind the same
`external_id` rewrites nothing. A fold changes neither row's keys, and history's
revert restores a changeset's earlier keys as they were, unless the restored row
would be a second live handle of an address another handle now owns: that revert
is refused on `value` (`ValueOwned`), and nothing is written. Each saves through
`Handle.save`, which derives the keys from the value it writes, so none leaves a
key behind. A resource
row that names another address under an xref a load already wrote is refused:
a new address is a row of its own. A seed row that would keep a second live
handle of an email address is refused too, and its load stops
(`ResourceLoadError`) with nothing written, as a seed repeating an exact value
does. The guard is the instance save: queryset
`update`, `bulk_update` and a `bulk_create` that updates on conflict bypass it,
and no path writes the keys that way.

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
keep their own gates. A person's private fields are the notes, the first-met note,
the handle count, the birthday and the anniversary (`read_private`); the name
parts and the nickname are identity, read by whoever reads the person, as are
the languages and the occupation. The existing `reader` relation includes private Party reads; use it only
when that wider access is intended. Both use the native record-access owner.

Parties' roles (`parties/role`, anchored by `PartiesRole`) say once, for every
party, what a per-record grant says for one: a **contact reader**
(`contact_reader`) reads every party, and the handles, addresses, relationships
and circle memberships that party holds (through the party, so a handle no party
holds yet, a mailbox's sender or a calendar's attendee, is no role's), and every
address-book folder and circle (they hold no party: their arms are constants), as
a `reader` does, private fields included; a **contact
manager** (`contact_manager`) also writes them as an `editor` does (correcting
and confirming addresses, adopting where adoption applies (through the role's
own members: a platform admin's reach of every role lends no adoption, as
`parties/person#adopt` has no admin arm; but a role's member may be a set, and
granting the platform admins' set, `angee/role:admin#member`, onto the contact
manager or admin role makes every platform admin a member, and so every
directory, whose owner is a platform admin, adopts the people it names), keeping two people
separate), but merges none: a merge retires the party merged away to the
trash, so `Party.MERGED_PERMISSION` is `merge_away`, which is that party's
write with `role_merge_away` (the contact admin) in place of `role_manage`
(`role_manage` there would let a manager merge, and so trash, without
restoring); a
**contact admin** (`contact_admin`) also deletes and transfers as an owner
does, which is where trashing a party sits (its `delete`), grants per-record
reads on a party or handle (their `share`, which backs `rebac_grantable`: a
grant outlives its granter's role, so `role_share` is the contact admin;
`role_manage` there would let a manager grant), reads every directory
and shares who reads its contacts (`parties/directory#share`). Connecting,
changing and removing a directory stay with its integration's writers and
platform admins. Each choice a deployment's architect may flip is one line in
`parties/party`: `role_private` (whether a contact reader reads private fields)
and `role_delete` (whether a contact manager trashes, and with it deletes, a
party, person or organization; the dependent rows, folders and circles stay with
the contact admin through `role_admin` and their own constants), and the four
decided here, `role_write_as_subject`, `role_merge_away`, `role_share` and
`role_withheld`. A role holder also reads the record tasks about every party
(`read_as_subject`) and sees people with an account in pickers, but writes,
shares, deletes and transfers none of those tasks: `role_write_as_subject` is
empty (`role_manage` there would lend a contact manager and admin the tasks'
write). Parties grants
the roles to nobody: a deployment grants membership in the Permissions Hub, which
lists them as Contact Reader, Contact Manager and Contact Admin, or with a
`kind = "grants"` resource row (`{resource: "parties/role:contact_manager",
relation: member, subject: <a user's xref>}`). Of an account's own person (a
colleague's, `Person.user` set) the roles read the identity and no more:
`parties/party#role_withheld` subtracts that person from every role arm beyond
`role_identity`, so no role reads their private fields, handles, addresses,
relationships, circle memberships or record tasks, or writes, merges, shares,
deletes, transfers or adopts them; pickers still show them. The person is the
user's own, which adoption lends no directory either; `nil` there gives an
account's person the same reach as any other. A
directory's contact readers keep exactly what they read without the roles.

A person's and an organization's record form follows what the reader may do on
that record: their types project `permissions` (`delete`, `merge_away`, `share`
and `write`), so the form is read-only without `write`, offers Trash and Restore
with `delete`, Share (and who reads the record) with `share`, and "Merge into…"
with `merge_away`. A person's or organization's `share` is its party's, delegated
whole: sharing it writes the grant on the party, the grantable. A list's selection
of one still opens "Merge into…"; its rows carry no projection, so the compare
page refuses whoever may not merge.

Adding an address or relationship to a party takes write on that party
(`create` on `parties/address` and `parties/relationship` in [permissions.zed](permissions.zed)):
the row changes the party's projection, which a two-way directory writes to its
card, so no read grant attaches anything.

Sharing a directory shares its contacts: `contact_reader`, granted on the
directory row through its share surface, reads the people and organizations its
live links name and the handles, addresses and relationships those links state
(`contact_read` in [permissions.zed](permissions.zed)). Connecting a directory
grants its owner `contact_reader` (`Directory.grant_owner_contact_read`, the one
writer), and every provision grants it to an existing directory's owner where
missing (`grant_directory_owner_contact_reads`). A transfer does not move it: a
person grants a later owner, and the previous owner keeps the grant until
someone removes it. The read is a stored grant alone, so a party's read plans
the directory chain only for an actor who holds one.
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

Identity matching (`PartyManager.reconcile_same_as`) writes normal RULE same_as
statements on the ordered Party pair for every two live parties of one kind linked
to one address (`PartyManager.same_person_links`). A party is linked when it holds
a handle of that person-matching key, or when a source that states its own record (a
person, a system of record, a directory, an import, a source's claim) names one for
it in a standing statement, a claim below review confidence included, whatever
its evidence names (the handle, or the source's record link); a claim re-stated
with more evidence is the same link and changes no pair. Any other
rule or extraction, parties' own suggesters among them (a shared name, a domain, a
twin spelling or alias, a signature), links nobody until it is named, so a shared name alone
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
mutation; at most 50 sets a request), either every certain pair awaiting review
(`certain_same_as_pairs`, listed page by page, at most 1,000 a confirm, from the
Review page's "Merge all certain pairs") or a selection from the People or
Organizations list.
There, one selected record opens "Merge into…" to choose the other, two open
their compare page, and three or more on an ungrouped list are the duplicate
sets the server finds among them (`PartyManager.selection_duplicate_sets`, under
the person's own read): the same-person pairs the person may review between
selected records (`selection_pairs`, the one rule), joined into connected groups,
with the records that have no duplicate selected left out and counted; three to
ten with no pair among them are one set, as selected, and above ten with none the
dialog says what to do. A grouped list gives a set per group. Under the list's
"Select all N" (`selectAllMatching`), a list grouped by one text merge field
gives every group its read matches, each a set, listed page by page through the
list resource's own groups read (at most 1,000 a confirm). The dialog lists the
sets before anything merges. Only an active person account that may merge every record away may (the party's
`batch_merge` permission, `merge_away & person->active_member`: a set's oldest
survives, so each member may be the one merged; a contact manager, who writes but
merges nothing away, is offered no set, and a set where exactly one record lacks
`merge_away` is not offered though it could merge with that record surviving, on
the compare page); a service or agent
account merges nothing, and no agent tool offers it. A set never holds an account's
own person (merged by hand, on the compare page), records of two kinds, a trashed
or merged record, or two records a person kept apart; each is refused for that set
with its reason, and a set merges exactly as the person confirmed it or not at all.
A pair is certain when both are people, neither is an account's own person,
exactly the two are linked to an address the pair cites, and both bear a real
name, none of the pair's cited addresses (a record named after its e-mail has no
name of its own), equal to the other's but for case, accents and hyphens. In each
set the oldest record survives and the others fold into it in key order, under one
lock pass: the oldest is the earliest `created_at`, which a source's creation time
sets (below), so the record its sources have known longest survives. A batch records no value as the person's choice: each record's
statements move to the survivor as their sources' own and the provenance ranking
decides what shows; a field the survivor leaves empty takes a merged record's
value as a plain value no one states (`Party.merge_values`). The dialog shows, for
each set that merges, the fields its records disagree on and the value that will
show, leaving out a field the person's `read__<field>` gate hides on any record.
Each set that may merge has its own Include toggle, off until the person ticks it
when the set carries a warning (its records state two different values of a field,
or one no longer matches its group; a field one record states and the others leave
empty is a fill the merge gives the survivor, no warning) or has changed from the state the person saw or ticked it in (a refreshed
preview, a refused set that became mergeable), and the merge sends only the
included sets, each with the digest of what
the dialog showed of it: a set whose shown names, addresses or sources changed
since is refused under the lock and asks the person to look again; a
person who wants to choose merges that pair on the compare page instead. A merge,
batch or not, reverts from its changeset. A person's `anniversary` is a merge
choice like their birthday; a party's `avatar` is not one yet. Merging a person
with an organization is refused by every merge.

The `people`, `organizations` and `parties` resources read canonical parties
(`PartyQuerySet.canonical()`: outside the trash, not retracted by every source,
and not merged away into a survivor the reader sees), as handles, addresses and
relationships do. A merged-away or trashed party is no list row, group,
repeated-values group, count, search result or picker candidate, and the People
workbench's count is the list's. Their by-id roots read the same rows; a party
id resolves through `party_record(id)`, which answers a merged-away id with its
survivor, and the record redirect (`/parties/records/<id>`, where references to a
party link), the Party form and the person's and organization's own pages follow
it: a page whose read misses passes the id to the record form's
`renderUnavailable`, and `PartyRecordRedirect` opens the survivor's page when
`party_record` names another party, so an old direct link to a merged-away
person opens their survivor for someone who reads both, and shows "Record
unavailable" to anyone else.

Two limits follow from that read:

- A party that was trashed but never merged leaves the lists, and nobody can
  find it in the interface to restore it until the framework has a trash
  surface. The shared `restore_record` verb still restores it by its id.
- A party that every one of its sources has withdrawn leaves the lists too,
  as it already left the People workbench's count and search.
