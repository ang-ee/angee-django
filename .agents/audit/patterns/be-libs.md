# Backend library-consistency inventory

Read-only audit of every third-party (non-stdlib, non-`angee.*`) library actually
imported across `src/angee/**` and `examples/notes-angee/src/{host,example}/**`
(Python; `runtime/`, `migrations/`, tests excluded). Canon = `docs/stack.md`
Backend table; declared deps = `pyproject.toml`.

Stdlib HTTP/crypto/hash/enum modules (`http.client`, `urllib`, `socket`,
`hashlib`, `hmac`, `secrets`, `base64`, `ipaddress`, `enum`) are noted only where
they bear on a canon concern (IDs, encryption, REST, enums).

---

## Enum-backed model fields
- canon (stack.md): `django-choices-field` — Angee adds the `StateField`
  semantic wrapper (`src/angee/base/fields.py:37`, wraps `TextChoicesField`,
  derives `max_length`, defaults `db_index=True`).
- usage: `StateField(choices_enum=...)` for lifecycle/status columns —
  `examples/.../notes/models.py:41` (`Note.status`),
  `src/angee/integrate/models.py:53` (`Capability.status`),
  `src/angee/iam/models.py:335` (`Account.status`),
  `src/angee/iam/models.py:711` (`Credential.status`) (4 sites).
- variant: plain `models.CharField(..., choices=<Enum>.choices)` for enum-backed
  columns that bypass `StateField` —
  `src/angee/iam/models.py:709` (`Credential.kind`, `CredentialKind.choices`),
  `src/angee/resources/models.py:24` (`Resource.tier`, `ResourceTier.choices`) (2 sites).
  Both store a finite `TextChoices` value in a `CharField` with a hand-set
  `max_length`, which is exactly what `StateField` exists to own.
- not-drift: `EventKind` (`src/angee/integrate/events.py:8`) and `StateFlow`
  (`src/angee/iam/oidc/state.py:20`, stdlib `StrEnum`) are constant registries,
  not persisted columns — no field involved, so no `StateField` expected.
- verdict: DRIFTED
- recommend: route every enum-backed *column* through `StateField`. Convert
  `Credential.kind` and `Resource.tier` from `CharField(choices=…)` to
  `StateField(choices_enum=…)` so choices/`max_length`/index/GraphQL-enum derive
  from the enum once instead of being restated.

## GraphQL types, resolvers, schema
- canon (stack.md): `strawberry-django` (declared as `strawberry-graphql` +
  `strawberry-graphql-django`) — Angee merges addon schema parts, adds
  `crud`/`changes` shortcuts, emits SDL, serves per name.
- usage: `strawberry` + `strawberry_django` throughout —
  `src/angee/base/graphql/{schema,crud,node,subscriptions,events,errors}.py`,
  `src/angee/base/{views,consumers}.py`, `src/angee/iam/schema.py`,
  `src/angee/operator/schema.py`, `examples/.../notes/schema.py`
  (11 strawberry files; `strawberry_django` at 5 sites). Core `graphql`
  primitives used only for the printer / error wrapper / introspection
  (`src/angee/base/graphql/errors.py:6`, `src/angee/operator/daemon.py:92`).
- variant: none. No `graphene`, `ariadne`, or hand-rolled schema.
- verdict: CONSISTENT
- recommend: keep. Single GraphQL owner.

## REBAC / authorization
- canon (stack.md): `django-zed-rebac[strawberry-django]` — Angee adds per-addon
  schema merge, reserved roles, actor resolver.
- usage: `rebac.*` across 19 files (`from rebac` ×16, plus `rebac.managers`,
  `rebac.resources`, `rebac.graphql.strawberry`, `rebac.field_visibility`,
  `rebac.backends`, `rebac.permissions_mixin`, `rebac.roles`, …) — e.g.
  `src/angee/base/{access,relations,deletion,models}.py`,
  `src/angee/iam/{models,identity,signals,schema}.py`,
  `src/angee/integrate/{models,scheduler}.py`,
  `src/angee/resources/managers.py`.
- variant: none. No competing authz lib; no parallel permission framework.
- verdict: CONSISTENT
- recommend: keep.

## Opaque external IDs
- canon (stack.md): `django-sqids` — Angee adds `SqidMixin` and a GraphQL
  boundary scalar.
- usage: `SqidsField` / `SqidMixin` for every public id —
  `src/angee/base/mixins.py:31` (mixin base), and per-model prefixed sqids at
  `src/angee/iam/models.py` (usr/vnd/eac/clt/crd), `src/angee/integrate/models.py`
  (whs + `Capability`), `examples/.../notes/models.py:37` (nte) (14 sites).
- not-drift: `hashlib`/`secrets` uses are content hashes, HMAC webhook
  signatures, OIDC state/PKCE tokens, and JWK cache keys
  (`src/angee/resources/loader.py:249`, `src/angee/integrate/webhooks.py:238`,
  `src/angee/iam/oidc/state.py:56`), not entity identifiers — no UUID/PK leak.
- verdict: CONSISTENT
- recommend: keep. (Minor DRY: `SqidMixin` declares `sqid = SqidsField(real_field_name="id")`
  with no prefix, and every concrete model re-declares `sqid` to add a prefix +
  `min_length=8`; the bare mixin field is always overridden — a framework
  ergonomics note, not a library inconsistency.)

## Encryption at rest
- canon (stack.md): `cryptography` — Angee adds `EncryptedField` (Fernet at rest,
  secret-by-type, HKDF-SHA256 per-column key).
- usage: `cryptography.fernet.Fernet` + `hazmat` HKDF only inside
  `src/angee/base/fields.py:13-15`. Encryption reads elsewhere go *through*
  `EncryptedField` (`src/angee/iam/models.py:740`, `src/angee/iam/credentials.py:50`).
- not-drift: `base64.urlsafe_b64encode` at `src/angee/iam/schema.py:913` is PKCE
  challenge encoding, not encryption.
- variant: none. No second crypto path, no manual Fernet outside the field.
- verdict: CONSISTENT
- recommend: keep. One encryption owner.

## History / revisions  (deliverable d — the split)
- canon (stack.md): TWO rows, intentionally split.
  `django-simple-history` → `HistoryMixin` marker (shadow history tables + revert,
  whole-row metadata). `django-reversion` → `RevisionMixin` marker +
  `revisioned_fields` (versioned field snapshots + field-level revert).
- usage (simple-history): `HistoryMixin` is a pure marker
  (`src/angee/base/mixins.py:83`); the composer emits the real
  `HistoricalRecords()` field into runtime for `HistoryMixin` subclasses
  (`src/angee/compose/runtime.py:251-252,312-318`); `simple_history` is wired in
  settings (`src/angee/base/settings.py:61` middleware, `:120` INSTALLED_APPS).
  No direct `import simple_history` in source — by design, it is build-time emit.
- usage (reversion): `RevisionMixin` (`src/angee/base/mixins.py:92`) holds
  `revisions` + `revert_to()`; `src/angee/base/signals.py:27-33` registers each
  loaded model's `revisioned_fields` with `reversion.register`.
- both on one model, on purpose: `Note(… HistoryMixin, RevisionMixin)` with
  `revisioned_fields=("body",)` (`examples/.../notes/models.py:20,27`) — docstring
  states metadata is audited via `history`, `body` is versioned via `revisions`.
- verdict: CONSISTENT (principled split, documented in stack.md and the model
  docstring: row-level audit vs declared-field rollback). Not redundant.
- recommend: keep both; the boundary is owner-clean (whole-row shadow vs
  per-field snapshot). No migration.

## Resource import/export + tabular formats
- canon (stack.md): `django-import-export` + `tablib` — Angee adds tiered
  manifests, xref ledger, frozen-tier policy.
- usage: `import_export` (`fields`, `resources`, `widgets`,
  `instance_loaders.BaseInstanceLoader`, `results.RowResult`, `utils`,
  `exceptions.ImportError`) at `src/angee/resources/{loader,widgets,managers}.py`
  (6 sites); `tablib` at `src/angee/resources/{loader,entries}.py` (2 sites).
- variant: none. No parallel CSV/openpyxl/pandas path.
- verdict: CONSISTENT
- recommend: keep.

## ASGI / WebSocket transport
- canon (stack.md): `channels` + `daphne` — Angee adds GraphQL subscription
  mounting.
- usage: `channels.{layers,auth,routing}` at
  `src/angee/base/{asgi,signals}.py` and `src/angee/base/graphql/subscriptions.py`
  (4 sites); `asgiref.sync` bridges (`sync_to_async`/`async_to_sync`) at
  `src/angee/iam/identity.py:9`, `src/angee/base/signals.py:9`,
  `src/angee/base/graphql/subscriptions.py:9`. `daphne` is wired as an
  INSTALLED_APP (`src/angee/base/settings.py:113`), the correct non-import usage
  for the ASGI server.
- variant: none.
- verdict: CONSISTENT
- recommend: keep. (`asgiref` is a Django/Channels transitive sync-bridge, not a
  separate concern — no row needed.)

## JWT / OIDC verification
- canon (stack.md): `pyjwt[crypto]` — Angee adds OIDC discovery and exchange
  orchestration.
- usage: `jwt`, `jwt.PyJWKClient`, `jwt.exceptions` at
  `src/angee/iam/oidc/client.py:11,14,15` (the only JWT site). OIDC HTTP
  discovery/exchange uses stdlib `urllib.request` (`oidc/client.py:9,266,286,311`).
- variant: none. No `python-jose`, no `authlib`.
- verdict: CONSISTENT
- recommend: keep.

## Aggregation / group-by resolvers
- canon (stack.md): `strawberry-django-aggregates` — Angee adds "Wiring to model
  metadata".
- usage: `strawberry_django_aggregates.AggregateBuilder` only at
  `examples/.../notes/schema.py:14`. ZERO framework wiring: no
  aggregate/group-by code anywhere in `src/angee/**`.
- verdict: DRIFTED (glue claim vs reality). Declared a framework dependency and
  stack.md promises Angee "Wiring to model metadata", but the library is consumed
  directly by the example consumer addon and the framework adds no glue.
- recommend: EITHER build the promised framework wiring in `src/angee` (e.g. a
  `crud`-style aggregate shortcut driven by model metadata) so the stack.md glue
  is real, OR downgrade the stack.md "Angee adds" cell to "used directly by
  addons" so docs match code. Pick one; today the code and the doc disagree.

## REST routing + payload schemas + MIME  (canon rows with no code)
- canon (stack.md): `django-ninja` (typed REST routing), `pydantic` (REST payload
  schemas), `python-magic` (MIME detection) — all for the "rare sidecar"
  REST/upload surface.
- usage: NONE. No `ninja`, `pydantic`, or `magic` import anywhere in scope, and
  none of the three is declared in `pyproject.toml`.
- verdict: UNSANCTIONED-DOC / phantom rows. These are stack.md rows with neither a
  declared dependency nor any code — they violate stack.md's own "a dependency
  change is complete only when the concern row here and the relevant manifest
  agree". They read as aspirational, but they live in the locked Backend table,
  not under "Proposed, Not Locked".
- recommend: move `django-ninja` / `pydantic` / `python-magic` into the
  "Proposed, Not Locked" section until a sidecar actually ships, OR add them to
  `pyproject.toml` when the sidecar lands. Until then the locked table overstates
  the stack.

## PyYAML  (declared + used, no canon row)
- canon (stack.md): none. There is no `pyyaml`/`yaml` row in the Backend table.
- usage: `import yaml` at `src/angee/resources/entries.py:12` (resource entry
  parsing); `pyyaml>=6` IS declared in `pyproject.toml:23`.
- verdict: UNSANCTIONED. A declared, actively-used runtime dependency with no
  owner row — the direct inverse of the django-ninja phantom rows, and a
  violation of "Add a dependency only with an owner row here."
- recommend: add a `pyyaml` Backend row (concern: "YAML resource-entry parsing",
  Angee glue: resources entry loader) so the table matches the manifest.

---

## Top inconsistencies (worst first)

1. **`strawberry-django-aggregates` glue is fictional (DRIFTED).** stack.md
   promises framework "wiring to model metadata"; `src/angee/**` contains none —
   only the example addon calls `AggregateBuilder`
   (`examples/.../notes/schema.py:14`). Either build the glue or restate the row.

2. **Three locked REST rows have no dependency and no code (phantom rows).**
   `django-ninja`, `pydantic`, `python-magic` are in the locked Backend table but
   absent from `pyproject.toml` and unused — they belong under "Proposed, Not
   Locked" or need a real sidecar + declared deps.

3. **`pyyaml` is an unsanctioned dependency (no owner row).** Declared
   (`pyproject.toml:23`) and used (`src/angee/resources/entries.py:12`) yet has no
   stack.md row — add the row.

4. **Enum-backed columns bypass `StateField`.** `Credential.kind`
   (`src/angee/iam/models.py:709`) and `Resource.tier`
   (`src/angee/resources/models.py:24`) use raw `CharField(choices=Enum.choices)`
   while sibling status columns correctly use `StateField` — restate them through
   the canon wrapper so choices/`max_length`/index/GraphQL-enum derive once.

### Sidebar (not a library inconsistency)
- SSRF address-resolution is partially duplicated between
  `src/angee/base/net.py:resolved_addresses` and
  `src/angee/integrate/webhooks.py:_resolve_public_addresses` — a within-framework
  DRY item, but both correctly use stdlib `socket`/`ipaddress` (no library drift).
- `SqidMixin`'s bare `sqid` field is overridden by every concrete model (prefix +
  `min_length`); ergonomics, not a library choice.
