# Backend pattern-consistency inventory

Read-only audit of `src/angee/**` and `examples/notes-angee/src/{host,example}/**`
(excluding `runtime/`, `migrations/`, tests). Canon = `docs/stack.md` +
`docs/backend/guidelines.md`. Line refs are anchors, not exhaustive.

## 1. Enum / choices modeling
- canon: A **persisted** choices column is `models.TextChoices` declared beside
  the model, surfaced on the field by `StateField(choices_enum=…)`
  (`docs/stack.md`: django-choices-field owns enum-backed fields; backend
  guidelines: "persisted choices live beside the model field, usually as
  model-owned `TextChoices`"). A non-persisted, in-memory finite set may be a
  stdlib enum.
- variant A — `models.TextChoices` (the enum), used three ways:
  - On a `StateField` column (the full canonical shape): `Note.Status`
    (notes/models.py:29 → :41), `AccountStatus` (iam/models.py:28 → :335),
    `CredentialStatus` (iam/models.py:38 → :711), `CapabilityStatus`
    (integrate/models.py:32 → :53). (4)
  - On a plain `models.CharField(choices=…)`, NOT `StateField`: `CredentialKind`
    (iam/credentials.py:19; column at iam/models.py:709
    `kind = models.CharField(max_length=32, choices=CredentialKind.choices)`).
    `EventKind` (integrate/events.py:8) is a `TextChoices` but is never a model
    field — it is the wire vocabulary for webhook event matching, stored inside
    a `JSONField` (`event_kinds`), so it is choices-as-vocabulary, not a column. (2)
  - On a plain `models.CharField(choices=…)` declared in a non-model module:
    `ResourceTier` (resources/tiers.py:9; column at resources/models.py:24
    `tier = models.CharField(..., choices=ResourceTier.choices)`). Adds a
    `from_value()` coercion classmethod the others lack. (1)
- variant B — `enum.StrEnum`: `StateFlow` (iam/oidc/state.py:20). Not persisted —
  it lives only in a cached `StateRecord` dataclass (not a Django row) and gates
  OIDC flow replay. (1)
- verdict: CONSISTENT (the divergence is intentional, by ownership)
- recommend: No migration. The flagged "divergence" (`AccountStatus`/`CapabilityStatus`
  = TextChoices, `StateFlow` = StrEnum) is correct under canon: TextChoices is for
  persisted columns; `StateFlow` is a non-persisted cache value with no DB column,
  so a stdlib enum is right and it should NOT become a TextChoices. The one true
  inconsistency is **status-style columns that skip `StateField`**: `Credential.kind`
  and `Resource.tier` are choices-backed `CharField`s that could be `StateField`
  for the same indexed-enum semantics the four `StateField` columns get. `kind`/`tier`
  are arguably descriptors not lifecycle states, so this is a judgement call — but
  if `StateField` is "the enum-backed model column," using a bare `CharField(choices=)`
  in some places is a small shape drift. Note `Resource`/`ResourceTier` live in the
  resources layer which cannot import from `angee.base`… actually `StateField` is in
  `angee.base.fields` and resources may import base, so the layering does not block it.

## 2. GraphQL authoring
- canon: native Strawberry; addons expose `schemas` in `schema.py`; use the
  `crud()`/`changes()` shortcuts for model CRUD/subscriptions where they fit
  (`docs/stack.md`, backend guidelines "GraphQL authoring is native Strawberry").
- variant A — `crud()` shortcut (wraps `strawberry_django.mutations.create/update`
  + a delete-preview resolver): notes mutations (notes/schema.py:227, :232). (1 addon, both surfaces)
- variant B — `changes()` subscription shortcut: notes console subscription
  (notes/schema.py:233). (1)
- variant C — `strawberry_django.offset_paginated` / `strawberry_django.node`
  query fields (the canonical query shape): notes (notes/schema.py:184-189),
  iam connections + console (iam/schema.py:468-500). (2 addons)
- variant D — hand-rolled `@strawberry.type` + `@strawberry.mutation` mutation
  classes, NOT `crud()`: `IAMMutation` (login/logout/OIDC, iam/schema.py:504),
  `IAMVendorMutation` (iam/schema.py:716), `IAMOAuthClientMutation`
  (iam/schema.py:755). The two admin CRUD-shaped ones (`IAMVendorMutation`,
  `IAMOAuthClientMutation`) carry a docstring explaining why they cannot use
  `crud()`: the const-backed `create = admin->member` gate is unsatisfiable for a
  not-yet-inserted sqid row, so they gate via `PlatformAdminPermission` at the
  GraphQL layer and create under `system_context`. (3 classes)
- variant E — hand-rolled `@strawberry.type` query with a plain `@strawberry.field`
  resolver returning a domain payload: `OperatorQuery.operator_connection`
  (operator/schema.py:24), `IAMQuery.current_user` (iam/schema.py:447),
  `NotesQuery.note_revisions` (notes/schema.py:192). Used where there is no Django
  model row to project (token bridge, session user, reversion versions). (3)
- variant F — `strawberry_django_aggregates.AggregateBuilder` for group-by/aggregate
  fields, with an Angee `get_queryset` hook for REBAC scope: notes
  (notes/schema.py:169). (1)
- verdict: DRIFTED (acceptably) — `crud()` is used by exactly one addon (notes).
  Every IAM admin write is hand-rolled.
- recommend: Keep `crud()` as canon for owner-gated, table-backed CRUD; keep
  hand-rolled mutations for (a) non-CRUD verbs (login, OIDC, unlink) and (b) the
  const-admin-gated creates that `crud()` provably cannot express (documented in
  `IAMVendorMutation`). The real smell to watch: `IAMVendorMutation` /
  `IAMOAuthClientMutation` re-implement create/update/delete by hand with
  `_input_values`/`_assign_values`/`_resolve_public_id`/`_delete_instance` helpers
  (iam/schema.py:952-988) that duplicate what `crud()` + `strawberry_django.mutations`
  do. If `crud()` grew a "create under system_context behind a permission_class"
  mode, these three could collapse into it — that is the one place GraphQL authoring
  genuinely forks for a reason that could be unified. Until then, CONSISTENT-by-intent.

## 3. Manager / QuerySet
- canon: surface query behavior through `Manager.from_queryset`; row-set behavior
  on managers/querysets (backend guidelines, "Find the owner").
- variant A — `Manager.from_queryset(QuerySet)`: `ResourceManager` (resources/managers.py:212,
  from `ResourceQuerySet`). The only use of the canonical Django idiom. (1)
- variant B — `RebacManager` subclass with inline manager methods (no QuerySet
  split): `AccountManager` (iam/models.py:224), `OAuthClientManager`
  (iam/models.py:414), `CredentialManager` (iam/models.py:598),
  `WebhookSubscriptionManager` (integrate/models.py:200), `UserManager(RebacManager,
  BaseUserManager)` (iam/models.py:66). (5)
- verdict: DRIFTED
- recommend: This is the single clearest structural inconsistency. Almost all IAM
  + integrate domain query/write behavior lives on `RebacManager` subclasses with
  methods written directly on the manager (`link`, `upsert_for_user`, `connected_for`,
  `deliver_event`, `sync_from_settings`), while resources alone uses the
  `from_queryset` split. Most IAM manager methods are upserts/grants that are
  genuinely manager-level (not chainable queryset ops), so they belong on the manager
  — `from_queryset` would be ceremony. BUT `CredentialManager.connected_for`
  (iam/models.py:616) returns a filtered, chainable queryset and is a textbook
  queryset method living on the manager; same for the inline `.filter(...).order_by(...)`
  chains in `AccountManager.owner_for`. Pick one rule: either (a) declare
  `from_queryset` is canon and move chainable read methods to a `*QuerySet`, or
  (b) declare "RebacManager subclass, methods inline" is canon for REBAC models and
  document that `from_queryset` (resources) is the non-REBAC exception. Right now the
  same concern (a chainable scoped read) is shaped two ways. Recommend documenting
  (b) as canon since 5:1 and REBAC managers can't trivially be `from_queryset`'d
  without losing `RebacManager` behavior, then noting resources is the plain-Django
  exception. Note also two near-identical helpers — `AccountManager.caller_fields` +
  `CredentialManager.caller_fields` both feed `_validated_manager_values`
  (iam/models.py:781) — that is a shared primitive done once, good.

## 4. REBAC wiring
- canon: structural, owned by `django-zed-rebac`; declare relations in
  `permissions.zed`; use field-backed (`// rebac:field=`) when an FK/O2O already
  represents the relation; const-backed (`// rebac:const=`) for synthetic universal
  reach; explicit tuple writes only when no field/const owns it (backend guidelines).
- variant A — const-backed `admin: angee/role // rebac:const=admin`: used uniformly
  for the platform-admin reach on every admin-gated definition — iam (5 defs:
  auth/user, auth/vendor, auth/oauth_client, auth/external_account, auth/credential),
  integrate (webhook_subscription), operator (connection), notes (note). (8 sites)
- variant B — field-backed `owner: auth/user // rebac:field=created_by`: ONLY
  notes/note (notes/permissions.zed:7). Every other `owner` relation is NOT
  field-backed and is written explicitly. (1)
- variant C — explicit tuple write via `grant_owner()` (base/relations.py:21,
  wraps `write_relationships`): `AccountManager.link` (iam/models.py:277),
  `CredentialManager.upsert_for_user` (iam/models.py:684),
  `WebhookSubscriptionManager.create` (integrate/models.py:214). `revoke_owner`
  in unlink (iam/schema.py:671). (4)
- variant D — role grant via `rebac.roles.grant/revoke`: superuser→`angee/role:admin`
  membership mirrored on save (iam/signals.py:64). (1)
- `system_context` shapes — two forms, both consistent with their owner:
  (i) ambient `with system_context(reason=…)` context manager (the dominant form,
  ~all writes), (ii) the `RebacManager.system_context(reason=…)` chainable manager
  method for a single scoped read/get (`UserManager` iam/models.py:74/80/86,
  iam/schema.py:391/820). Form (ii) is the library's manager API for one-shot scoped
  queries; form (i) wraps a transaction. Not drift.
- verdict: CONSISTENT for const (8/8 identical) and `system_context`; the
  owner/field-vs-explicit split is intentional but worth a note.
- recommend: No change to const usage — it is the most uniform pattern in the
  backend. The `owner` relation is field-backed only where a stable pre-insert FK
  exists (`Note.created_by`); IAM/integrate `owner` grants are written explicitly
  because the relation has no backing FK column (the owner is a REBAC subject, not a
  model field) or the row's sqid id isn't known until post-insert — so `grant_owner`
  is the right tool there, not a missing field-backing. This is consistent-by-ownership.
  Minor: `iam/signals.py` uses `rebac.roles.grant/revoke` (the role API) while
  `base/relations.py` uses `write_relationships`/`delete_relationships` (the tuple
  API) — two REBAC write APIs, but for two different concerns (role membership vs
  resource ownership), so correct.

## 5. Model mixin application
- canon: mixins are abstract bases applied by inheritance; apply the ones the model
  needs (base/mixins.py). `AngeeModel` already carries `TimestampMixin` + `RebacMixin`.
- Observed application across concrete-emitting source models:
  - `SqidMixin, AuditMixin, AngeeModel`: Vendor, ExternalAccount, OAuthClient,
    Credential (iam/models.py), Capability, WebhookSubscription (integrate/models.py).
    The dominant shape. (6)
  - `SqidMixin, AuditMixin, AngeeModel, HistoryMixin, RevisionMixin`: Note
    (notes/models.py:20) — adds history + revision because notes needs them. (1)
  - `SqidMixin, AbstractBaseUser, RebacPermissionsMixin, AngeeModel`: User
    (iam/models.py:152) — no `AuditMixin` (correct: a user does not audit itself),
    swaps in Django auth bases. (1)
  - `AngeeModel` only: Resource (resources/models.py:12, a ledger — no sqid/audit),
    OperatorConnection (operator/models.py:18, a table-less type anchor). (2)
- Mixin/MRO ordering: every model lists `SqidMixin` first and `AngeeModel` after the
  audit/auth mixins, but `Note` appends `HistoryMixin, RevisionMixin` AFTER
  `AngeeModel`, while all others put `AngeeModel` last. Two different positions for
  `AngeeModel` in the base list.
- `EncryptedField`: applied to every secret column uniformly — `OAuthClient.client_secret`,
  `Credential.material`, `WebhookSubscription.secret` (3 sites). Consistent.
- verdict: CONSISTENT (functionally) with a cosmetic ordering wrinkle.
- recommend: No behavioral change — mixins are applied where needed and nowhere
  spuriously. Optional tidy: standardize base-class declaration order so `AngeeModel`
  sits in the same MRO position everywhere (Note puts `HistoryMixin, RevisionMixin`
  after `AngeeModel`; the others end with `AngeeModel`). Since `HistoryMixin`/`RevisionMixin`
  are markers with no fields, order is harmless, so this is style-only.

## 6. Validation
- canon: coerce/validate on the owning field (`Field.to_python`/validators) the
  Django way; raise `ValidationError` for field/form-level invalid input; manager
  input contracts raise `ValueError`.
- variant A — Django field validator function passed to a field: `validate_public_url`
  (base/net.py:53) on `WebhookSubscription.target_url`
  (integrate/models.py:280 `validators=(validate_public_url,)`); `UnicodeUsernameValidator`
  on `User.username` (iam/models.py:161). The canonical field-owned shape. (2)
- variant B — field subclass enforcing its own contract at construction:
  `EncryptedField.__init__` rejects `unique`/`primary_key` (base/fields.py:73),
  `get_lookup` rejects value lookups (base/fields.py:128). Behavior on the field. (1)
- variant C — `raise ValidationError` from a non-field helper (SSRF address checks):
  base/net.py (6×), integrate/webhooks.py (4×). These are call-time network checks,
  not field validation, but reuse `ValidationError`. (10)
- variant D — `raise ValueError` for manager/handler input contracts:
  `_validated_manager_values` (iam/models.py:792), `OAuthClientManager` setting
  validation (iam/models.py:474/477/504/508), credential handler `validate()`
  (iam/credentials.py:85/126), rollup status guards (iam/models.py:755/773). (≈11)
- variant E — `raise ImproperlyConfigured` for composition/declaration-time invalid
  config: apps.py (11×), settings.py (2×), entries.py (14×), fields.py (4×). (many)
- verdict: CONSISTENT — each tier uses the error class that matches its layer
  (ValidationError = field/HTTP-input, ValueError = manager/runtime contract,
  ImproperlyConfigured = declaration/build).
- recommend: No change. The one thing to watch: `WebhookSubscription` validates its
  URL via a field validator (good) but `PinnedWebhookClient` re-checks at delivery
  via `ValidationError` in webhooks.py — that is deliberate (DNS rebind defense), and
  `base/net.py` is documented as the single owner of "is this URL safe outbound,"
  reused by both. Good DRY.

## 7. Error / exception style
- canon: custom exception classes only when they carry data callers branch on;
  otherwise Django/stdlib exceptions.
- variant A — custom exception carrying structured data: `OidcFlowError(code,
  http_status, body)` (iam/oidc/errors.py:16), `WebhookDeliveryError(message,
  status)` (integrate/webhooks.py:32), `ResourceLoadError(RuntimeError)`
  (resources/exceptions.py:6). Each carries a field consumers read (code/status). (3)
- variant B — stdlib/Django exceptions: `ValueError` (manager contracts),
  `ImproperlyConfigured` (declaration), `ValidationError` (field/URL),
  `NotImplementedError` (abstract `Bridge`/handler methods, integrate/models.py
  :164/:169/:174/:187/:192, iam/credentials.py:42/47/115). (many)
- variant C — internal control-flow exception: `DryRunRollback` (raised/caught in
  resources/managers.py:117 to roll back a dry run) — defined in resources/loader.py. (1)
- verdict: CONSISTENT
- recommend: No change. Custom classes exist only where a caller branches on carried
  data (`error.code`, `error.status`), matching canon. OIDC error codes are
  module-level string constants (iam/oidc/errors.py:7-13) reused across client.py +
  identity.py + schema.py — a single source of truth, good.

## 8. Settings access
- canon: settings helpers are pure functions of args (`compose_defaults`); they do
  NOT read the environment. Addon defaults flow through `settings_defaults` on
  `AppConfig`. Runtime reads of optional settings use `getattr(settings, …, default)`.
- variant A — `compose_defaults(**explicit_args)` pure helper + addon
  `settings_defaults` mapping merged beneath it: the framework composition path
  (base/settings.py:29, base/apps.py:54, iam/apps.py:15). The host applies the
  returned mapping in one `globals().update()` (host/settings.py:45). Canonical. (1)
- variant B — `getattr(settings, "NAME", default)` for optional runtime settings:
  iam/models.py:466, iam/oidc/client.py:334, iam/oidc/state.py:95, base/views.py:25,
  operator/daemon.py:53/54/127. (7)
- variant C — `OperatorDaemon._setting()` reads BOTH `getattr(settings, …)` AND
  `os.environ.get(…)` (operator/daemon.py:122-133). This is the only code that reads
  the environment directly. It is justified in-comment (the operator bridge resolves
  daemon URL/token that `angee dev` exports as env vars), and `OperatorDaemon` is a
  runtime bridge object, not a settings helper — so it does not violate the
  "settings helpers don't read env" rule. But it is a distinct third shape.
- verdict: CONSISTENT (with one documented env-reading exception)
- recommend: No structural change. `compose_defaults` purity and `settings_defaults`
  are uniform. Flag `OperatorDaemon._setting` as the lone env reader: backend
  guidelines say "the host owns reading the environment." Today the daemon reads env
  directly with a settings fallback. Consider whether the host should resolve these
  into settings (like IAM's `ANGEE_IAM_OAUTH_CLIENTS`) and the daemon read only
  `getattr(settings, …)` — that would align it with variant B and keep env-reading at
  the host. Low priority; it is isolated and documented.

## 9. Factory / serialization idioms
- canon: classmethod factories `from_*` and `deconstruct`-style methods construct/
  serialize objects from their owner (backend guidelines).
- variant A — `from_*` classmethod factory: `AngeeModel.from_public_id`
  (base/models.py:93), `ResourceEntry.from_declaration` (entries.py:120),
  `ResourceRow.from_record` (entries.py:319), `ResourceTier.from_value` (tiers.py:17),
  `DeletionPreview.from_instance` (deletion.py:79), `OperatorDaemon.from_settings`
  (daemon.py:44), `GraphQLSchemas.from_discovery`/`from_addons` (schema.py:48/54). (8)
- variant B — `from_domain` GraphQL-projection factory (domain dataclass →
  `@strawberry.type`): `DeletePreview*.from_domain` (crud.py:30/46/71). (3)
- variant C — `from_payload` / `from_version` mapping/external-object → type:
  `ChangeEvent.from_payload` (events.py:23), `NoteRevision.from_version`
  (notes/schema.py:60). (2)
- variant D — `from_db_value` (Django field-owned deserialization): `EncryptedField`
  (fields.py:109). Django-native, correct on the field. (1)
- No `deconstruct` overrides found (no custom field needs custom migration state;
  `StateField`/`EncryptedField` rely on parent `deconstruct`).
- verdict: CONSISTENT
- recommend: No change. Naming is uniform (`from_*` verb-first, matching the Naming
  section). The `from_domain` vs `from_payload` vs `from_version` split is by source
  shape (domain object / dict / external version), which is fine. One micro-note:
  `AccountManager.owner_for` (iam/models.py:280) reconstructs a User from a REBAC
  subject_id by hand with a comment "REBAC exposes SubjectRef creation publicly, but
  not inverse model lookup" — a documented gap in the library, not an Angee
  inconsistency.

## Top inconsistencies (worst first)
1. **Manager vs QuerySet split (DRIFTED, §3).** 5 REBAC managers carry methods
   inline; resources alone uses `Manager.from_queryset`. The same concern — a
   chainable scoped read — is shaped two ways (`CredentialManager.connected_for`
   returns a queryset from a manager method; `ResourceQuerySet` puts chainable ops on
   a queryset). Pick and document one canon (recommend: "RebacManager subclass,
   inline methods" is canon; resources `from_queryset` is the non-REBAC exception),
   and move purely chainable reads off the IAM managers if (a) is chosen instead.
2. **GraphQL admin CRUD is hand-rolled, not `crud()` (DRIFTED-by-intent, §2).**
   `crud()` is used by exactly one addon (notes). `IAMVendorMutation` /
   `IAMOAuthClientMutation` re-implement create/update/delete with local
   `_input_values`/`_assign_values`/`_delete_instance` helpers because const-admin
   `create` can't gate a pre-insert sqid. The reason is real and documented, but the
   duplication of CRUD mechanics is the largest copy in the GraphQL layer; a
   `crud(..., elevated=True, permission_classes=[…])` mode would unify it.
3. **Status columns that skip `StateField` (minor, §1).** `Credential.kind` and
   `Resource.tier` use bare `CharField(choices=…)` while four sibling status columns
   use `StateField`. If `StateField` is "the enum-backed column," these are a small
   shape drift (defensible as descriptors, not lifecycle states — a judgement call).

Everything else is CONSISTENT or consistent-by-ownership: const-backed REBAC admin
(8/8 identical), `system_context` usage, mixin application, validation tiering,
exception style, `compose_defaults` purity, and `from_*` factories. The lone
env-reading site (`OperatorDaemon._setting`) and the field-backed-vs-explicit
`owner` grant split are both intentional and documented.
