# IAM + Roles Lift — backend & frontend (roles-as-namespace)

Lift the auth/identity surface from P1 (`../angee-django-p1/addons/auth`) into this
repo's **`iam`** addon, using the **roles-as-namespace** model we settled on the
whiteboard. This is the backend counterpart the frontend plan's §1.6 auth UI
(`frontend-stack-upgrade-popover-dry.md`) assumes exists, plus the role
architecture. **Reviewed** by `plan-reviewer` + `django-reviewer` (2026-06-02);
their findings are folded in below.

> **Reconcile, don't contradict.**
> - `.agents/plans/notes-auth-lift.md` is the **one plan** (SSOT). This file is a
>   focused sub-plan; it must not contradict it. It **resolves D-C** there
>   (`currentUser.roleRefs`) and honors §1 (no codegen, hand-written `schema.py` +
>   `crud()`/`changes()`, `iam.User` label `iam`, `contrib.auth` stays, no
>   sys.modules shim, lift hygiene).
> - `.agents/plans/frontend-stack-upgrade-popover-dry.md` §1.6 owns the auth **UI**
>   surface. **Placement reconciled (D6): `src/angee/iam/web/`** — §1.6's stale
>   `src/angee/auth/web/` example is corrected to `iam/web`.

---

## 0. The model (locked on the whiteboard)

- **Roles = `<namespace>/role:<id>` Zed objects**, single `member` relation, granted
  to principals by **one relationship tuple** via the library convention
  `rebac.roles` (`grant / revoke / roles_of / members_of / imply / unimply /
  implies_of / implied_by_of`). **No Django `Role` model, no FK, no M2M.**
- **A role's "bundle of permissions" is not stored** — it is *distributed across the
  schema*: wherever a resource's permission expression references
  `<ns>/role:<id>#effective_member`, that role gains that permission on that type.
- **Role→resource-type binding = const-backed relation** (this repo's idiom:
  `relation admin: angee/role // rebac:const=admin` + `permission … = admin->member`,
  already shipped in `iam/permissions.zed` and `notes/permissions.zed`). **Do NOT lift
  P1's per-instance `materialize_role_subject_sets` / `rebac_roles.py`** — const-backing
  superseded it ([[const-backed-relations]]).
- **`iam` owns identity** (the principal records + the `roles_of` projection);
  **the rebac layer owns authorization** (role catalog in `.zed`, grants as tuples).
- **App label `iam`; REBAC namespace `auth/*`** (already the split in
  `iam/permissions.zed`). Keep it.

### Locked decisions
- **D1 — full scope:** users, groups, services + API keys, impersonation, roles,
  grants, role hierarchy, relationships, permission-schema, login.
- **D2 — drop** P1's inert `Permission` / `PermissionsMixin` M2M. REBAC is the only authority.
- **D3 — author runtime role hierarchy:** `<ns>/role` carries `includes` +
  `effective_member`; resources reference `…#effective_member` so `imply`/`unimply` are real.
- **D4 — defer custom (runtime-authored) roles** behind the binding-object /
  `SchemaOverride` seam. Ship the **predefined** per-addon role catalog only.
- **D5 — adopt** P1's `ANGEE_AUTH_SERVICE_TOKENS` settings map for service auth.
- **D6 — frontend root `src/angee/iam/web/`** (matches the backend label; correct §1.6).
- **D7 — `currentUser.roleRefs` = DIRECT grants only** (`roles_of`), documented as a
  **UX hint**; the server stays the authz boundary (implied roles won't light up menus).
- **D8 — `ImpersonationEvent` FKs `on_delete=PROTECT`** (audit retention); the delete
  preview must report the block cleanly.

---

## 1. Critical constraints (grounded in this repo)

> **Central correctness theme (django-reviewer):** P1 was written for
> `REBAC_SUPERUSER_BYPASS=True`; **this repo is fail-closed**
> (`base/settings.py`: `REBAC_STRICT_MODE=True`, `REBAC_SUPERUSER_BYPASS=False`).
> Every P1 pattern that assumed a loose posture (`_base_manager` as an unscoped
> escape hatch, `last_login`/`last_used_at` writes, principal CRUD) **fails closed
> here** unless it gets an explicit `system_context`/`asystem_context` bracket or a
> const-backed `admin` schema reach. This is the root cause behind B2/B3 below.

- **All GraphQL is hand-written.** P1's model-`Meta` machinery
  (`queryable`/`mutable`/`subscribable`/`graphql_private_fields`/`search_fields`) has
  **zero consumers here** (verified) — only `rebac_resource_type` / `rebac_id_attr`
  are read (`compose/runtime.py:456`). Each principal type is a hand-written
  `@strawberry_django.type` (private fields simply not declared, as
  `iam/schema.py:UserType` already does) + `crud()` (`base/graphql/crud.py:57`) +
  `changes()` (`base/graphql/subscriptions.py:19`).
- **Subject resolution is owned by the `@rebac_subject` decorator registry, NOT Meta.**
  `to_subject_ref`/`rebac.roles.grant` resolve an instance→`SubjectRef` only via the
  decorator. Meta `rebac_resource_type` governs *resource* scoping (reads/writes of
  the row); it does **not** make a row usable as an *actor*.
- **`rebac.roles` is available** — `iam/signals.py` already uses `grant`/`revoke`; the
  full API is in the path-overridden sibling `../django-zed-rebac` (`src/rebac/roles.py`).
- **Lift hygiene:** reconstruct, never copy; no file byte-identical to P1; strip all
  provenance; reuse local primitives. P1 paths are **feature references**, not sources.
- **Per-area gate green before commit;** new deps carry a `docs/stack.md` owner row.
- **Run from root;** emit→migrate→`rebac sync`→`resources load`→`schema --check`.

### Framework gaps to fill first (owner = `base`)
- **`AvatarField`** does not exist (`base/fields.py` has only `StateField`). Add a
  minimal `AvatarField` to `base` with a **deterministic `deconstruct()`** (static
  importable `upload_to`, no lambda / wall-clock / random) — migrations must be
  reproducible (CLAUDE.md deterministic-artifacts rule). Owner = base, one stack row.
- Confirm `crud()`/`changes()` cover create/update/delete + subscription for the new
  types; extend `base` if a gap surfaces (owner = base).

---

## 2. Backend phases

P1 feature references: `addons/auth/angee/auth/{models,actors,backends,signals}.py`,
`.../graphql/{auth,identity,permissions,contribution}.py`, `addons/auth/rebac.zed`.

### B1 — Principal models (`src/angee/iam/models.py`)
- **Extend `User`:** `avatar` (AvatarField), `preferences` (JSON). Keep swappable,
  label `iam`, no shim. **Mirror sync↔async system-context routing on the manager:**
  add `aget` / `aget_by_natural_key` that route through `system_context` (the sync
  `get`/`get_by_natural_key` already do; the async side is the documented gap).
- **`Group`** (`auth/group`, sqid `grp`): name, description. **No** `permissions` M2M (D2).
- **`Service`** (`auth/service`, sqid `svc`): name, description, `owner`→User, is_active.
  **Decorate `@rebac_subject(type="auth/service", id_attr="sqid")`** (actor resolution).
- **`ApiKey`** (`auth/apikey`, sqid `key`): name, `service`→Service, `on_behalf_of`→User,
  `token_hash` (**unique**, sha256), `token_prefix`, `expires_at`, `revoked_at`,
  `last_used_at`. **Decorate `@rebac_subject(type="auth/apikey", id_attr="sqid")`.**
  - `is_valid`, `matches()` via **`hmac.compare_digest`**; lookup = `filter(token_hash=…)`
    then `matches()`. Docstring the rationale: **sha256 (not Argon2) is correct for
    high-entropy random tokens** — a slow KDF buys nothing and adds per-request latency.
  - `last_used_at` is bumped with an **unscoped queryset `_base_manager.filter(pk=…).
    update(last_used_at=…)`** — never `instance.save()` (that trips the write gate and
    bumps `auto_now` `updated_at`).
  - `issue()` / `revoke()` run their writes under `system_context(reason="iam.apikey.*")`
    (framework op) **or** the GraphQL mutation carries `permission_classes` — pick one
    owner and document it.
- **`ImpersonationEvent`** (`auth/impersonation_event`, sqid `imp`): actor, target
  (**both `on_delete=PROTECT`**, D8), reason, started_at, stopped_at.
- Each principal carries `rebac_resource_type` + `rebac_id_attr="sqid"` in Meta only
  (+ standard Django Meta). **No** P1 Meta-machinery keys.
- **Migrations:** generate the User extension + all FK-bearing principals in **one
  `makemigrations iam`** so the swappable-user dependency order is co-resolved; `migrate`
  then `makemigrations --check` must be drift-clean (B7).

### B2 — Zed schema (`src/angee/iam/permissions.zed`) — author BEFORE crud()
With fail-closed settings, **every** create/update/delete is gated by the `.zed`
permissions, so the full write schema must exist before B5 exposes `crud()` (else the
intermediate commit denies all principal writes — incl. admins, since admin reach is the
*declared* const-backed relation). Reconstruct to this repo's idiom + D3:
- `auth/group`, `auth/service`, `auth/apikey`, `auth/impersonation_event` definitions:
  owner/editor/viewer relations + `create`/`read`/`write`/`delete` permissions + the
  **const-backed `admin: angee/role // rebac:const=admin`** line on every definition
  (matching `auth/user`) so platform admins keep reach with bypass off.
- **`auth/role`** with the D3 shape:
  ```
  definition auth/role {
      relation member:   auth/user | auth/group#member | angee/role:admin#member
      relation includes: auth/role#effective_member
      permission effective_member = member + includes
  }
  ```
  Resources reference `auth/role:<id>#effective_member` (not `#member`).
- Extend `auth/user` with `editor` → `auth/role:identity_admin#effective_member |
  angee/role:admin#member` and create/read/write/delete/impersonate permissions.
- **Predefined role catalog** = the `<id>`s the schema references (start: `identity_admin`).

### B3 — Actor resolver + credentials (`src/angee/iam/actors.py`, `backends.py`)
- **Resolver** built on the named stack primitives: two small declining resolvers
  (`Bearer`→`ApiKey` via `rebac.actors.bearer_token`; `X-Angee-Service`→`Service` via
  the D5 token map) composed with **`rebac.actors.chain_resolvers(api_key, service)`**,
  terminal = the library `default_resolver` (session user / anon). Build refs via
  **`to_subject_ref(instance)`** (one path; relies on the B1 `@rebac_subject` decorators
  — do not reintroduce P1's `_decorated_subject_ref` attribute helper).
  - **Async/ASGI:** keep P1's sync/async split — sync resolvers wrapped in
    `sync_to_async(thread_sensitive=True)`; **never a bare sync ORM call in an `async
    def` branch** (raises `SynchronousOnlyOperation`). Keep
    `_ensure_async_request_user_is_concrete` (replace the `SimpleLazyObject`).
  - Wire as `REBAC_ACTOR_RESOLVER` via `settings_defaults`.
- **`UsernamePasswordBackend`** (timing-flat). The auth backend **owns the no-actor
  credential/session fetch** — wrap it in `system_context`/`asystem_context`; do **not**
  rely on `_base_manager` being unscoped (iam's `UserManager` overrides routing).
- **`settings_defaults`** adds `REBAC_ACTOR_RESOLVER` + `AUTHENTICATION_BACKENDS`.
  Note: the composer treats **conflicting keys across addons as an error** (not
  last-write-wins); no other in-tree addon sets these today.

### B4 — Role seed + signals
- Keep `iam/signals.py:sync_platform_admin_role` (superuser→`angee/role:admin`) — already
  shipped + correct (the `update_fields` short-circuit correctly skips the `last_login`
  save). No materialization signal.
- **Demo grants need a real owner** (resources tiers CANNOT write `Relationship` rows —
  `rebac sync` syncs schema only; `AngeeResource` is a model `ModelResource`). Add an
  **idempotent seed callable** (e.g. an `iam` management step / seed function run after
  `migrate` + `resources load`) that calls `rebac.roles.grant(actor=alice,
  role="auth/role:identity_admin")` for the demo principals. There is **nothing to seed
  at "install"** — catalog role IDs exist purely because B2's `.zed` references them.
- Honor the superuser bootstrap caveat (grant via a `save()` path / re-grant).

### B5 — GraphQL (`src/angee/iam/schema.py`, hand-written) — explicit buckets
Schema buckets (reuse the existing two named schemas; do not coin new ones):
**`console`** = principal CRUD + the permission hub (admin surfaces); **`public`** =
identity self-service + `currentUser.roleRefs`.
- **Principals:** hand-written `@strawberry_django.type` for User (extend), Group,
  Service, ApiKey, ImpersonationEvent; `crud()` (create/update/delete) with
  `permission_classes`; `changes()` subscription for User. Wrap `grantRole`/`revokeRole`/
  mutations in `transaction.atomic`; defer non-DB side effects with `transaction.on_commit`.
- **Permission hub** — owner map (reconstruct P1 `permissions.py` *features* smaller than
  its ~1630 lines; **drop P1's `_role_*`/`_subject_*` row decoders** — `ObjectRef`/
  `SubjectRef` own that shape):

  | GraphQL verb | Owner |
  | --- | --- |
  | `grantRole` / `revokeRole` / `implyRole` / `unimplyRole` | `rebac.roles.{grant,revoke,imply,unimply}` (thin) |
  | `rolesOf` / `membersOf` / `impliesOf` / `impliedByOf` | `rebac.roles.{roles_of,members_of,implies_of,implied_by_of}` |
  | `grants` / `roleBindings` / `relationships` | `Relationship` model queries |
  | `permissionsSchema` | schema introspection (the parsed `.zed` AST) |

  **Paginate every listing (OFFSET, the repo convention)** and **batch principal-label
  loads** (one `in`-query per subject type / a strawberry-django dataloader) — do not
  resolve a Django principal per ref (N+1). `resource_type__endswith="/role"` is a
  suffix match on a joined column; fine per-actor, must be paged for global listings.
- **Identity:** `changePassword`, `issueApiKey`, `startImpersonation`/stop,
  `updatePreferences`; **`currentUser.roleRefs = roles_of(actor)` (D7: direct grants,
  UX hint).** Wrap any `auth_login` / `last_login` write in `system_context` (sync) /
  `asystem_context` (async) — prefer disconnecting Django's default `update_last_login`
  receiver and writing `last_login` explicitly inside the bracket (one owner).

### B6 — docs/stack.md + settings
- Owner rows for `AvatarField`, `ANGEE_AUTH_SERVICE_TOKENS`, and any new dep.

### B7 — Backend verify
`angee build` → `makemigrations iam` (one run) → `migrate` → `makemigrations --check`
(drift-clean) → `rebac sync` → `resources load` → seed grants → `schema --check`.
pytest: **non-superuser** login advances `last_login` over **both** the sync test client
and an **async/ASGI** path (Bearer ApiKey, assert no `SynchronousOnlyOperation`); apikey
+ service actor resolution; role grant/revoke; `imply`/`unimply` hierarchy; const-backed
admin reach; **admin can CRUD each principal, a normal user cannot**;
`currentUser.roleRefs`; `DeletionPreview` reports the PROTECT block (not a crash).

---

## 3. Frontend phases (placement `src/angee/iam/web/`, D6)

Reconciles with `frontend-stack-upgrade-popover-dry.md` §1.6 (auth UI ~31 files,
storybook-first). Stage-1 = presentational stories; Stage-2 = wire to B5.

- **F1 — Login (public):** `LoginPage` + `UsernamePasswordForm` + login-method slots
  (OAuth from the integrate-auth-oauth addon) + hero + safe-next-path → wire to
  `login` / `currentUser`.
- **F2 — Principals console:** users / groups / **machines (services + API keys)**
  list+form views, `PrincipalAvatar`, row-adapters → B5 principals CRUD.
- **F3 — Permission hub:** roles, grants (`GrantComposer` / `RevokeGrantButton`), role
  bindings, role implications, relationships, permission-schema pages → B5 hub
  queries/mutations. Roles shown = the **catalog** (`auth/role:*` ids + presentational
  labels); grants = tuples. Every list paginated.
- **F4 — Client gating:** `currentUser.roleRefs` → `hasRole(...)` for menu/route
  visibility — **UX-only (D7); direct grants, server is the boundary.** Document that
  implied (hierarchy) roles won't light up the menu.

---

## 4. Sequencing & gates

1. base gaps (`AvatarField` w/ deterministic deconstruct; crud/changes coverage) — owner=base.
2. B1 models → **B2 zed (full write schema)** → B3 resolver/backend → B4 seed/signals →
   B5 GraphQL → B7 verify. One phase ≈ one commit. B2 **must** precede B5.
3. Frontend F1→F4 follows B5 (Stage-2 wiring); Stage-1 stories can run in parallel.
4. Each backend phase: **`django-reviewer`**; structural slices: **`architecture-reviewer`**.
   Frontend slices keep the §1.6 three-review cadence.

---

## 5. Deferred / open

- **Custom (runtime-authored) roles (D4):** binding-object indirection or Tier-2
  `SchemaOverride` rows — design note only, not built.
- **django-admin parity:** dropped the inert M2M (D2); revisit only if django-admin
  user/group screens are required.
- **Per-addon role catalogs:** storage/agents/operator/etc. ship their own `<ns>/role`
  blocks when those addons are lifted (tracked by the addon-backend gap, not here).
- **`DeletionPreview` non-CASCADE handling:** the PROTECT FKs (D8) need the carried-over
  `base/deletion.py` block-reporting fix (flagged in `notes-auth-lift.md`) — confirm it's
  closed before User delete ships.
