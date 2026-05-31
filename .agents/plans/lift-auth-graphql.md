# M1 — IAM + REBAC↔GraphQL + relay/aggregates (notes + auth end-to-end)

> **Executor (Codex):** build slice by slice on the current branch (base verified: gate
> green, fresh-ledger e2e loads 11 rows). Reconstruct, never copy; **no provenance** in
> code/commits; **no codegen**; **less code is better** — defer to the rebac /
> strawberry-django / strawberry-django-aggregates / Django stack; build only the thin
> seams. The concrete, verified wiring + exact lib symbols are in
> **`.agents/notes/lift-auth-graphql/RECIPE.md`** (Pagination+sqid / Aggregates / Auth —
> read it). Re-read files before/after edits. Per-slice + final gate below. Commit per slice.

**Goal:** a client can (1) log in (username+password, **session cookie**) and read
`currentUser`; (2) do REBAC-row-scoped CRUD + **relay-paginated** list/single + **nested**
reads of notes addressed by opaque sqid; (3) get REBAC-scoped change subscriptions over WS;
(4) see denials as clean GraphQL error codes; (5) run **server-side aggregates**. Identity =
our composed `iam.User`; REBAC owns authz; the framework owns the schema seams + the IAM
schema + the login verbs.

**Locked decisions (full detail in RECIPE.md + STATE.md):**
- **Auth = REBAC-native, app `angee.iam`.** `iam.User` = `AbstractBaseUser` +
  `rebac.RebacPermissionsMixin` + angee mixins (Timestamp + **sqid**), composed +
  `AUTH_USER_MODEL="iam.User"`. Groups/roles/permissions owned by REBAC (`iam/permissions.zed`,
  `RebacBackend` single authz source; no Django permission M2Ms). `contrib.auth` STAYS
  installed (rebac/channels import its `Group`/`AnonymousUser`); **no shim**.
- **Identity/pagination:** one `AngeeNode(strawberry.relay.Node)` base declaring
  `sqid: relay.NodeID[str]`; wire id = standard relay `GlobalID(base64 Type:sqid)` (no custom
  scalar); `Connection = strawberry_django.relay.DjangoCursorConnection` (keyset); total
  ordering `(..., 'sqid')` is a framework invariant.
- **Universal REBAC extensions** in `GraphQLSchemas.build()`:
  `[RebacExtension, *parts['extensions'], RebacDjangoOptimizerExtension]` (the **rebac**
  optimizer subclass, LAST; no custom actor extension — ActorMiddleware owns the HTTP actor).
- **Denial codes** via a `strawberry.Schema` subclass overriding `process_errors`
  (`PermissionDenied`→`PERMISSION_DENIED`, `MissingActorError`→`UNAUTHENTICATED`).
- **Aggregates** = `strawberry-django-aggregates` **compute layer only**
  (`compiler`/`operators`/`granularity`/`errors`); author the GraphQL types by hand;
  **eager REBAC scope** (managed manager → actor → `compute_aggregation`). M1 surface = `count`
  + group_by (`word_count` is a `@property` → promote to a real field for sum/avg, or defer).
- **F5:** addon GraphQL module `graphql.py`→`schema.py` (one discovery hook) — fixes
  `manage.py test`.

---

## Slice 1 — Framework GraphQL seams (identity + extensions + denial codes)
**Files:** new `src/angee/base/graphql/node.py`; `src/angee/base/graphql/__init__.py`;
`src/angee/base/graphql/schema.py`; new `src/angee/base/graphql/errors.py`;
`src/angee/compose/runtime.py` + delete `src/angee/compose/rebac.py`; `src/angee/base/apps.py`
(F5 discovery hook); `tests/test_graphql.py`, `tests/test_layering.py`.
- `AngeeNode(strawberry.relay.Node)` with `sqid: relay.NodeID[str]`; export it +
  `Connection = DjangoCursorConnection` from `base/graphql/__init__.py`. (RECIPE → Pagination+sqid)
- `build()`: prepend/append the universal REBAC extensions (rebac optimizer subclass, order
  load-bearing). (RECIPE → Pagination+sqid; aggregates uses same `extensions` bucket)
- `errors.py`: `strawberry.Schema` subclass overriding `process_errors`; `build()` instantiates
  it. (RECIPE → Auth/denial)
- Delete the combined `permissions.zed` emit (`runtime.py:67`) + the now-dead `compose/rebac.py`
  (`render_permissions`/`write_permissions`) + its import; re-emit runtime so `build --check`
  is green; reconcile `docs/backend/guidelines.md:133`.
- F5: rename the addon GraphQL discovery from `graphql`→`schema` (the `graphql_module`
  cached_property + `import_optional_module(...)` in `apps.py`); update `tests/test_apps.py`
  stubs + docs (`guidelines.md:137,155-156`, `glossary.md:75`).
- Boot invariant: assert every GraphQL-exposed `RebacMixin` model's `_default_manager` is a
  `RebacManager`.
- **Verify:** `build()`/`render_sdl()` succeed; an unauthorized write → `PERMISSION_DENIED`;
  no-actor read → `UNAUTHENTICATED`; `build --check` green; layering test still green; existing
  suite green (some schema-exec tests may need an actor — login via TestClient or
  `system_context`).

## Slice 2 — WS consumer on the library mixin (supersede round-2's hand-roll)
**Files:** `src/angee/base/consumers.py`, `src/angee/base/graphql/subscriptions.py`,
`tests/test_subscriptions.py`.
- Replace whatever `ce8afa9` left with `class AngeeGraphQLWSConsumer(RebacChannelsConsumerMixin,
  GraphQLWSConsumer)`; delete any hand-rolled scope/handler code. Subscription gate reads
  `rebac.current_actor()` (pass into `ChangeReadGate(model, actor)`); confirm it's visible in the
  `sync_to_async(thread_sensitive=True)` thread before dropping any explicit wrap. (RECIPE → Auth
  lib-owned)
- **Verify:** `noteChanged` delivers gated events to an owner, filters for a non-owner. **depends_on:** 1.

## Slice 3 — `angee.iam` addon + composed `iam.User`
**Files:** `src/angee/iam/{__init__,apps,models,permissions.zed}.py`;
`src/angee/base/settings.py` (register `angee.iam` in BOTH app sets; `AUTH_USER_MODEL="iam.User"`).
- `IAMConfig(BaseAddonConfig)` name `angee.iam` label `iam` depends_on `("base",)`.
- Abstract source `User(AbstractBaseUser, rebac.RebacPermissionsMixin, <angee mixins incl. sqid>)`
  + `BaseUserManager`; composed → `runtime/iam/models.py`; `AUTH_USER_MODEL="iam.User"` set in
  `compose_defaults` (both sets). **⚠ verify the swappable-composed-user wiring** (resolves in
  the run set, set before first migration, doesn't break the emit-only build set — mirror p1).
- `permissions.zed`: `auth/user`, `auth/group`, `angee/role` admin set (W004). (RECIPE → Auth)
- **Verify:** `angee build` emits `runtime/iam/models.py` (concrete User, `app_label="iam"`);
  `makemigrations`/`migrate` create the user table; `rebac sync`/`check` pass.
- **depends_on:** none (parallel-ish); needed before 4/5/6.

## Slice 4 — Real demo users for `iam.User`
**Files:** `examples/notes-angee/src/example/notes/resources/demo/010_auth.user.*` (retarget to
`iam.User`, `password = make_password(...)`).
- **Verify:** fresh-ledger `resources load` creates the users;
  `authenticate(username, password)` succeeds. **depends_on:** 3.

## Slice 5 — IAM GraphQL verbs (login / logout / currentUser)
**Files:** `src/angee/iam/schema.py` (note `schema.py`, F5); contribute to `public` + `console`.
- `UserType(AngeeNode)` over `iam.User`; `Mutation.login` (`authenticate`+`login` → session),
  `.logout`; `Query.currentUser` (`info.context.request.user`, `None` anonymous). (RECIPE → Auth)
- **Verify (over the HTTP view, cookie):** login alice → `currentUser` returns alice → logout
  clears; anonymous → `None`; denial codes apply. **depends_on:** 1, 3, 4.

## Slice 6 — Notes read-side: relay connections + opaque id + DX
**Files:** `examples/notes-angee/src/example/notes/schema.py` (renamed from `graphql.py`);
`src/angee/base/graphql/crud.py` (F8); `examples/.../notes/models.py` (Meta.ordering).
- `NoteType(AngeeNode)` via `@strawberry_django.type(Note)`; delete hand-rolled `id()/sqid()`;
  `notes: Connection[NoteType] = strawberry_django.connection()`,
  `note: NoteType | None = strawberry_django.node()`; delete manual list/single resolvers; fix
  `word_count` → `@strawberry_django.field(only=['body'])`. Demonstrate one **nested connection**.
  `Note.Meta.ordering = ('-updated_at','title','sqid')`. (RECIPE → Pagination+sqid)
- F8: crud `update` by opaque id (relay `GlobalID`/sqid), consistent with `delete`.
- **Verify:** as alice, `notes()` relay-paginates only alice-readable notes in `-updated_at`
  order with `first/after`; as bob, alice's rows filtered at root AND nested; `node(id:)` +
  edge `node.id` round-trip the sqid; `word_count` computes; `manage.py test` no longer hits the
  graphql-core shadow. **depends_on:** 1, 5, 3.

## Slice 7 — Server-side aggregates
**Files:** `pyproject.toml`/`uv.lock` (add `strawberry-django-aggregates>=0.2.2`);
`examples/notes-angee/src/example/notes/schema.py`.
- Import the **compute layer only**; author `NoteAggregate`/`NoteGrouped` types by hand; resolver
  eager-scopes (managed manager → actor → `compute_aggregation`). M1 = `count` + group_by over
  `status`/`is_starred`/`updated_at(month)`. Add an owner-map/lint note forbidding
  `.builder/.types/.relations/.pagination` imports. (RECIPE → Aggregates)
- **Verify:** an aggregate/group-by query returns correct server-computed, actor-scoped values.
- **depends_on:** 6.

---

## Final gate (must be green) + end-to-end
```sh
uv run ruff check . --no-cache && uv run mypy src/ && uv run pytest
uv run examples/notes-angee/manage.py angee build --check
uv run examples/notes-angee/manage.py test    # F5: no graphql-core shadow
```
Fresh-ledger e2e (isolated `ANGEE_DATA_DIR`, HTTP view for reads — not raw `execute_sync`):
`angee build → makemigrations → migrate → rebac sync → resources load --include-demo →
login(alice) → currentUser → notes() relay-paginated & scoped (root + nested) → an aggregate
query → noteChanged gating → a denied op → PERMISSION_DENIED / UNAUTHENTICATED.`

## Executor notes
- Independent: 1, 3. Then 2←1; 4←3; 5←1,3,4; 6←1,5,3; 7←6.
- Blocker → record in `STATE.md` and keep going on independent slices; don't stop.
- Don't edit `docs/stack.md` (its aggregates owner-row already names the lib) or frontend files.
- The `iam.User` composed-swappable-user wiring is the riskiest piece — verify carefully (Slice 3).
