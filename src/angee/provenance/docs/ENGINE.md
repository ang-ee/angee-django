# Provenance engine contracts

The capture and statement engine lives in `angee.provenance.engine` inside the
`angee.provenance` addon. The addon is the sole Django app with label `provenance`.
Its existing public modules compose the engine with Angee policy; see the
[addon README](../README.md) for the application contract.

## 1. Scope and ownership

The engine provides logical changesets, native history capture, deterministic
export, safe atomic replay, idempotent creation claims and typed retained
statements with deterministic source selection. The addon installs through
`INSTALLED_APPS`; composed projects own their native migrations. The engine
modules remain independent of Angee permission, GraphQL and UI policy.

Django owns transaction/savepoint behavior, routing, model identity and fields.
django-simple-history owns typed snapshots, historical descriptors and diffs.
The engine extends these owners for missing bulk capture and provenance rules.
The surrounding addon contracts own authorization, external sync, merge decisions
and retained removal.
Restoring database state never claims to undo remote side effects.

## 2. Public configuration and host policy

Declare `PROVENANCE_CHANGESET_MODEL = "provenance.Changeset"` to select the
composed concrete storage. Django's native swappable migrations require this
setting explicitly; system check E004 reports a missing declaration. The addon
subclasses `angee.provenance.engine.base.AbstractChangeset`. That abstract
module can be imported without registering concrete storage. `PROVENANCE_STRICT`
defaults to true. The `PROVENANCE_POLICY` setting optionally names a policy class by dotted import
path. Settings resolve lazily, including under `override_settings`.

`angee.provenance.engine.policy.Policy` defines `authorize(actor, action, instance)`,
`source_kind(source)`, `resolve_source(source, using=...)` and `history_user(actor)`.
Source resolution proves native persistence on the selected database without
granting permission; hosts may decode their explicit principal identities there. `get_policy()` resolves it.
Actions use Django permission verbs: `view`, `add`, `change`, `delete`, `export`.
Default admission requires an authenticated actor's Django model permission;
unknown actors fail closed. Capture itself never invokes a permission grant or
changes ambient authority. Statement writes use the canonical subject's `change`
permission. Replay authorizes every affected subject before mutation. Lossless
export requires `export_changeset`. APIs returning raw ORM objects are trusted
application APIs; hosts must authorize and redact public representations.

`actor_context(actor, change_reason='')`, `current_actor()`, `current_reason()`
and `actor_reference(actor)` live in `context.py`. A model actor is represented
as its model label and primary key; an explicit string is an opaque attribution
reference, never a permission or a statement principal. Statements require
persisted Django principals, classified by the host policy or an explicit
`source_kind` contract. Default policy classifies Django auth users as persons.

## 3. Capture and changeset boundaries

`changeset(actor=..., action=..., activity=..., change_reason=...,
correlation_id=..., origin=..., occurred_at=..., fresh=False, process=False)`
supports sync and async context management and yields a failure callback.
An explicit `using` binds attribution to that database and refuses a conflicting
write or nested join; an unbound boundary retains separate native rows per alias.
`current_changeset(required=False, record=False)` returns lazy storage;
`require_changeset()` requires it. `mark_changeset_failed()` records handled
failure. Empty boundaries create no rows and work before migrations.

An ordinary nested boundary joins/refines its owner before the first write;
conflicting explicit facts fail. `fresh=True` creates a distinct logical write
with entrypoint defaults which a nested domain boundary may refine before its
first write. Once writing starts, conflicting facts always fail. Capture
is context-local; copied contexts and process-owned boundaries support worker
threads without sharing thread-local database connections. Rows belong to a
writing thread, database and actor. Savepoint rollback must not leave a stale
changeset primary key; later writes recreate rolled-back attribution. Closing
the owner records success/failure only for committed changesets. A failure flag
does not itself roll back an application's writes; `transaction.atomic` owns
that choice.

`HistoryMixin` opts ordinary models into capture, using `ModelHistory`,
`CaptureManager` and `HistoryQuerySet`. Explicit `ModelHistory` declaration is
supported. History is captured on saves, expressions, queryset updates, bulk
create/update, deletes and collector cascades. Capture reads authoritative
stored values, including deferred and partially updated rows. A write group
produces one version per affected concrete row; proxy/MTI dispatch must not
duplicate versions. Each MTI parent uses its own Django primary-key value;
parent queryset capture follows the declared child link when a child has an
independent primary key. Generated/excluded-only field updates are not history.
Conflict-ignoring inserts that cannot identify changed rows fail before writing.
Native upserts (`update_conflicts=True`) retain `~` snapshots: Django's bulk API
does not report which rows were inserted. Replay safely refuses a row without
prehistory in such a batch; use ordinary inserts when reversible creation is
required. A refusal rolls back the entire replay.
`SIMPLE_HISTORY_ENABLED=False` disables capture without hiding stored history.
Selected base/default managers must preserve captured querysets.

Native `_history_user`, `_history_date`, `_change_reason` and bulk history
attributes remain supported. Historical `history_changeset` attributes capture independently of every copied
source field, including an ordinary source `changeset`. `ChangesetAttribution`
continues to mean source last-writer attribution; `HistoryAttribution` owns
historical metadata. Capture sets `history_source_complete=True`; the field's
False schema default prevents legacy rows from claiming recovered source state.
Incomplete snapshots remain queryable with their explicit marker.
ModelHistory.source_state_complete owns availability, including supplied
predecessors; the refusal guard composes that predicate. Lossless export,
diffs and replay refuse the incomplete snapshots they require. Capture never rewrites retained snapshots.

Existing historical schemas require a host-owned migration: rename the old
historical `changeset` attribution field to `history_changeset`, then recreate
any source `changeset` field using native simple-history field copying. Source
values overwritten by the old implementation cannot be recovered from those
rows; leave their completeness marker False. Rows from models without this
collision may be marked complete only after their retained source projection
is verified. Never seed missing source values from current state or attribution.
Custom historical bases must compose `HistoryAttribution`; the old
`ChangesetAttribution` source base is not historical capture metadata. Source, historical and changeset models follow
Django database routing; a deployment requiring atomic cross-table capture must
co-locate those writes. Cross-database distributed atomicity is not promised.
Explicit database bindings must not silently select another database.
The plain engine default retains `source_database`. A host preserving a legacy
co-located schema may pass `bases=(HistoryAttribution, ...)` and
`use_base_model_db=True` to `ModelHistory`; without that explicit co-location,
a historical model lacking source identity is rejected at registration. Such
history uses its actual database alias as source identity and retains its
co-located source identity without adding a source_database column. The capture
metadata transition above remains required. Custom bases with `source_database` retain the ordinary strict
source check. Replay refuses a boundary whose
source rows are not co-located with its history/changeset storage, before changing
any row. Export retains that source identity.

## 4. Changeset storage, queries, claims, export

`AbstractChangeset` owns actor reference, action/activity/reason, origin and
created-result generic references, occurred/recorded timestamps, open/closed
state, outcome, touched model index, counts, claim scope/key/fingerprint and
optional document digest. The addon supplies the swappable concrete storage; each
composed project owns its initial migration. History FKs protect retained attribution.

`Changeset.objects.touching(record)` and `records_touched()` compose record
document scopes, including concrete ancestors, declared reverse-FK owned rows
and statement tables. A record's `history_relations` is the declared owned
document membership; arbitrary references are not ownership.

`claim(scope, key, fingerprint=...)` requires an outer atomic transaction and a
fresh, unwritten boundary. A conditional unique constraint arbitrates races.
Matching retries return the existing receipt; mismatched content raises
`CreationKeyConflict`. `record_created(row)` attaches the result;
`claim_created(...)` verifies its expected type and availability.

`document(actor=...)` exports deterministic lossless JSON for a closed boundary,
with stable ordering and explicit encodings for Django-native non-JSON field
values. Its digest excludes itself. Closure writes bookkeeping without scanning
history; export calculates the digest. No archive sink or pruning is shipped.

## 5. Replay

`Changeset.revert(actor=...)` restores the state before each affected row's first
write and records a fresh changeset. It rejects open/failed changesets, later
touches (even equal timestamps), unavailable prehistory and unsupported cycles.
It authorizes and validates the entire operation before mutation and uses an
atomic transaction and deterministic locks. Native history IDs settle ties.
Restoration orders FK/MTI parents before dependents; removal reverses dependency
order. Removal preflight uses Django's Collector and refuses cascades or field
updates outside the boundary. A subject may override `revert_removal` and
`revert_removal_collector` for retained-removal behavior. Reverting a revert is
supported. Statement assertions are retained and re-resolved, not erased. A
statement subject needs its host's retained-removal hook to undo its creation;
the default hard deletion refuses protected evidence instead of erasing it.

`ModelHistory` owns replay extension methods `changeset_states`,
`authorize_revert`, `revert_order`, `stage_revert`, `revert_row`. `HistoryMixin`
owns `history_tables`, `history_identity_columns`, `history_scopes`,
`admitted_reverts`, `revert_subjects` and `revert_removal`. Statement history
extends these methods. Callers do not infer statement internals.

## 6. Typed statements

Statement registration is independent of subject history: `StatementsMixin`
does not install `HistoryMixin`. `StatementRecords(aspects={name: Aspect(...)}, link=None)` uses native
simple-history field copying to generate one retained typed statement model per
declaring subject. Native `bases`, `history_manager`, `historical_queryset` and
`excluded_fields` constructor contracts compose host row/set behavior without a
second generator; custom declarations may extend native metadata hooks. `StatementsMixin` supplies subject-owned eligibility and
application. `Aspect` declares canonical fields, typed members, precedence,
optional filling and application hooks. Source kinds do not imply a global
precedence. `StatementSourceMixin` and `StatementLinkMixin` are plain abstract
Django contracts; authoritative links can raise system-of-record precedence
within an aspect, without defeating preferred person/seed assertions.

The subject-bound manager owns `set`, `withdraw`, `withdraw_unsupported`,
`resolve`, `confirm`, `dismiss`. `provenance(field)` reports the applied
assertion. Source, kind, rank, stance, status and applied state are separate
facts. Normal and preferred ranks are supported; preferred is limited to person
and seed. Value, some-value and no-value stances are distinct. Current-key
uniqueness includes subject, source, aspect and typed members. Withdrawn and
superseded assertions remain queryable and historized. Public arbitrary update,
bulk mutation and deletion must not bypass statement ownership.

Selection is deterministic and subject-locked. Operational fields with no
assertions are preserved. Invalid field values or cross-database principals fail
before writes. Evidence remains typed references or JSON-compatible facts;
public readers must follow host permission/redaction policy.

An ordinary person save on aspect-backed fields asserts normal statements under
that persisted person. It must survive a later lower-priority import. Resolution
and replay use the subject's explicit application hook without recursively
creating assertions. Fields outside declared aspects retain ordinary Django
save behavior, and untouched fields are never asserted by a partial save.

`PairAspect` records symmetric identity or keep-separate assertions. It never
chooses a survivor or triggers a merge. Explicit host merge policy remains the
owner of operational consolidation. Statements participate in ordinary native
history and changeset replay, including withdrawal of initial assertions and
subsequent re-resolution.

## 7. Entrypoints

Sync/async HTTP middleware and ASGI WebSocket middleware open one boundary per
request/scope and mark errors failed. Streaming-response iteration is outside
the HTTP boundary; a writing iterator must open its own. Optional Celery task
integration is an extra. Management commands and jobs use explicit changesets;
there is no monkey patch of Django commands or task frameworks.

## 8. Integration notes

The original plain-Django assertions now live under `tests/provenance_engine/`
with an isolated settings module. The framework's addon tests exercise its
Angee policy and composed storage.

Capture-only hosts can compose `history.CaptureHistoryMixin` with their own replay/removal owner. `history.HistoryMixin` retains the standalone composition with `HistoryReplayMixin`; capture does not require adopting native deletion policy.

`Aspect[Subject]` binds candidate and native apply callbacks to their owning
Django model subtype. `StatementRecords[Subject]` preserves that binding across
its aspect mapping; model-general callback-free aspects can share the same
declaration. Annotate a standalone callback-free aspect with its subject when
no declaration context supplies that type. A callback for another model is
rejected rather than widened to a callable accepting every Django model.

`replay.HistoryScopeMixin` is the fieldless record/ancestor/owned-relation history
identity contract without capture, permission or replay/removal policy.
`HistoryReplayMixin` composes it. Forward scopes and inverse touched-record
queries resolve each identity through Django's actual ancestor primary-key or
foreign-key target field; independent MTI child keys do not replace parent keys.
Hosts can extend the existing table/column declarations for their related history.

The engine scope table set remains the current model and captured ancestors.
A host overriding `history_tables()` to include linked sibling/descendant views
uses their retained common-ancestor fields, not their independent primary keys.
The runtime-injected `save_without_historical_record` keeps Django Model.save's
native static signature on the capture-only mixin too.
