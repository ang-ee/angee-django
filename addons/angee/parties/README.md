# Directory synchronization

A Directory composes the [record-sync driver](../integrate/README.md). Each
address book maps to a Folder and contacts stream; RecordLink owns external
identity and wire payloads. Party rows remain independent of any one directory.

The [CardDAV backend](../parties_integrate_carddav/backend.py) compares the shared
[contact projection](backends.py); wire properties outside that projection stay
in RecordLink history and survive conditional writes.

[PartyManager.ingest_contact](managers.py) asserts directory facts; Address and
Relationship keep their own identities and statements. Missing members withdraw
only that source. Canonical scopes retain unasserted rows and exclude trash and
wholly withdrawn rows. [Handle sources](managers.py) compose `Party.statements`
through the shared Handle, Party lock order; resolution owns the cross-party
winner and derived counters outside Party history.

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

A handle that proves to be another's spelling (a resolved messenger alias) folds
into the handle that owns the contact point on the same platform through
`Handle.objects.merge(alias, into=kept)`: the record fold every merge shares
(`angee.base.merge.fold_records`), one merge changeset under the identity lock
order, refusing what any record merge refuses. The kept handle fills only the values it
lacks (owner, label, display name, verification, avatar, transport metadata keys).
Handle statements move through `Party.objects.move_handle_statements`, the generic
reference move a record merge performs: collisions stay inspectable as superseded,
so every decision and its evidence survive. Every other pointing row (messages,
participations, reactions, tags) takes the same generic move, which drops a copy
of a fact the kept handle already holds; then the alias is deleted.

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

Source admission follows RecordLinks and seeded Rule/Seed role membership.
Sources and migrated carrier readers gain no private reads. Imports supply their
Integration and observed links; parties binds targets in the application transaction.

Identity matching writes normal RULE same_as statements on the ordered Party
pair, skipping current assertions and dismissals before each run's cap without
continuation state. A preferred no_value is the symmetric anti-link; preferred value
awaits a person's survivor choice. `survivor.merge` owns the changeset and redirect;
pointing owners union statements, retaining collisions as superseded.

## Upgrading from PartyHandle carriers

The [declared chain](addon.toml) folds legacy `PartyHandle`, CardDAV columns and
`MergeVeto` rows into statements, then retires them. Migration docstrings own the
retained evidence, discarded legacy data and reversal contracts; the rules are:

- **Preflight first.** `preflight_party_folds` evaluates both folds' classification
  on the pre-upgrade state and fails with every blocking row (model, pk, reason)
  before any destructive declaration of provenance, integrate or parties runs. It
  also prints what the folds leave unasserted. Remediate (attribute or remove the
  named rows) on the old release, then migrate again; nothing has been dropped.
- **Nothing is attributed to an invented actor.** Rule and matcher rows fold under
  their seeded `Rule` key (`normalized_twin`, `email_domain`, `display_name`,
  `signature_phone`, `source_sender`); the OIDC self-claim is the owning user's
  preferred statement; a person's confirm/dismiss is their preferred stance; a
  service-account or actorless decision is a normal statement by the fold's `Seed`
  principal (`parties.fold_party_handle`) keeping the recorded account as evidence.
  A takeout import (`import` with no Integration) is one IMPORT source per imported
  takeout account: the importer's channel whose backend key is the handle's platform
  asserts it; without exactly one such channel the link, like imported relationships
  and the identity values of imported parties, stays unasserted and is counted. A
  directory value equal to the applied snapshot is
  the directory's; a local edit is the editing person's, or stays unasserted.
- **The preflight covers the retirements too.** Enrollment identity, link retention,
  extension data and MergeVeto references are checked before anything drops; a card no
  link retains is reported, its local values stay unasserted and the column drops.
  spaces' roster-label drop waits for the parties statement schema, so it runs after the
  preflight on a stack with parties.
- **Parity before drops.** Each retirement proves the folded evidence before its
  `DeleteModel`/`RemoveField`; a card's bytes count as retained when a link
  observation is the same vCard (lines compared as a set, `REV`/`PRODID` ignored).
  Stale `source_etag` values are discarded with the column.
- **Another addon's fold joins through a declared family.** An addon whose own fold
  writes parties statements in a migration (an import's system of record) appends a
  callable returning its fold changesets' change reasons to
  `ANGEE_PARTIES_STATEMENT_FOLDS`, writes them as system work with native history,
  and never overwrites an applied statement: it records its own beside it. Folds do
  not rank each other, so every migrate ranks the groups a family's statements share
  with another writer, once per fold changeset newer than the last successful
  ranking (`PartyManager.resolve_folded_statements`); a failure fails that migrate and
  the next one ranks again. The retirement accepts such a fold's statement on an
  import carrier this chain's fold left unasserted.
- **Lost by ruling:** every django-reversion revision (MarkdownPage body, Project
  title/body history) — back the database up first if that history matters. After
  the upgrade run `manage.py remove_stale_contenttypes` for the `reversion.*`,
  `parties.partyhandle` and `parties.mergeveto` content types.
- **Reversal:** the folds are no-ops backwards; `retire_party_handle` recreates an
  empty table and does not undo JSON rewrites, edge retargets or deleted grants,
  and the old code rejects the rewritten `statement_id`, so revert the whole chain.
