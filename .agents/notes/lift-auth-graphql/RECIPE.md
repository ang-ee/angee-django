# M1 build recipe — aggregates + auth (from investigation wxafgtbrk, verified vs venv)

Concrete detail behind `.agents/plans/lift-auth-graphql.md`. Library-first, no codegen.

## Aggregates — `strawberry-django-aggregates`, compute-layer only
- **Add dep:** `strawberry-django-aggregates>=0.2.2` to `pyproject.toml` + `uv.lock`
  (named in `docs/stack.md:32`, not yet installed; floor satisfied by target pins).
- **Import ONLY the pure compute layer** (Django+zoneinfo only, never strawberry):
  `from strawberry_django_aggregates.compiler import compute_aggregation, group_by_alias, aggregate_alias`
  `from strawberry_django_aggregates.operators import AggregateOp`
  `from strawberry_django_aggregates.granularity import TimeGranularity, NumberGranularity`
  `from strawberry_django_aggregates.errors import ...`
  **NEVER import** `.builder` / `.types` / `.relations` / `.pagination` (the codegen layer
  — `AggregateBuilder`, `make_*`, `StaticViewIndex`, `info.selected_fields` walking). Add an
  owner-map/lint guard forbidding those imports.
- **Author the GraphQL types BY HAND** (plain `@strawberry.type`/`@strawberry.input`) in the
  addon's types module; contribute through the addon's `schemas` dict
  (`schemas['public']['query'].append(NotesAggregateQuery)`, `['types'] += (...)`). No change
  to `GraphQLSchemas`, no SDL splicing, no emitter. `compute_aggregation(qs, *,
  group_by=[(field, granularity|None)], aggregates=[(AggregateOp, field|None)], having,
  order_by, offset, limit, ...) -> list[dict]`; read rows by canonical keys from
  `aggregate_alias`/`group_by_alias` (e.g. `count`, `sum_word_count`, `status`,
  `updated_at_month`). Resolver knows its measures statically → NO selected-fields walking.
- **REBAC scoping is the critical correctness rule.** `compute_aggregation` is
  permission-NAIVE and its `.values().annotate()` projection BYPASSES `RebacQuerySet`'s lazy
  scoping (which only fires in `_fetch_all`/`count`/`exists`). So scope **eagerly, in this
  order, inside the resolver (resolver-time, after `RebacExtension` pinned the actor — never
  at build time):** (1) start from the **managed manager** `Model.objects.all()` (a
  `RebacQuerySet`; NEVER `_base_manager`); (2) apply the actor — rely on the ambient actor
  + `qs._apply_scope_in_place()` (bakes `<id_attr>__in=<accessible>` into the WHERE;
  `accessible()` is memoised per actor/action/type) OR explicit
  `qs.with_actor(current_actor())`/`.as_user(user)`; (3) THEN search; (4) THEN
  `compute_aggregation(scoped_qs, ...)`. GROUP BY/HAVING build on the already-filtered qs, so
  every bucket is actor-scoped. Keep `allow_relation_traversal` OFF (its Subquery reaches the
  unscoped leaf manager) — aggregate the child model directly with its own scoped manager. An
  aggregate resolver returns a dataclass → naturally bypasses `RebacDjangoOptimizerExtension`
  (no `disable_optimization` needed). Ensure `RebacExtension` is contributed in
  `schemas[...]['extensions']` ordered FIRST.
- **MEASURE GAP (decide):** `Note.word_count` is a Python `@property`, not a DB column → can't
  SUM/AVG. **M1 default: ship `count` + group_by over `status`/`is_starred`/`updated_at`
  (month)** — proves server-side aggregate+group-by with no numeric measure. To expose
  sum/avg, promote `word_count` to a real `PositiveIntegerField` kept in sync (follow-up).
- **SQLite:** stick to `count/sum/avg/min/max`; Postgres-only ops raise
  `OperatorNotSupportedError` at resolver entry.

## Pagination + sqid (nested) — `wcrxlmdol`, all primitives already in the target
**The target already has it all; only wiring is missing.** `Note.sqid =
SqidsField(real_field_name="id", prefix="nte", min_length=8)`, `Meta.rebac_id_attr="sqid"`,
`AngeeModel(RebacMixin)` → `_default_manager` is a `RebacManager` (auto row-scope), `Meta.ordering`,
`ActorMiddleware` + `REBAC_STRICT_MODE=True` all present. Libs installed: `DjangoCursorConnection`,
`strawberry_django.node/connection`, `RebacDjangoOptimizerExtension`.

**Resolved decisions (autonomous, clear rationale):**
- **Wire id = standard relay `GlobalID` (base64 of `Type:sqid`)**, NOT a bare-sqid scalar_map.
  Less code, cross-model uniqueness via the relay TypeName, standard client tooling. Keep the
  prefixed sqid as a separate `sqid`/`publicId` OUTPUT field for human URLs. (Escalate only if a
  human URL must EQUAL the GraphQL id → needs p1's `scalar_map={relay.GlobalID: SqidScalar}` +
  a per-model prefix-uniqueness invariant.)
- **Connection base = `DjangoCursorConnection`** (keyset, insert-stable, no pk leak), aliased as
  `angee.base.graphql.Connection`. Requires a **total ORDER BY** → append a unique tiebreaker:
  `Note.Meta.ordering = ('-updated_at','title','sqid')` (framework invariant for every queryable
  model).
- **One identity seam:** `src/angee/base/graphql/node.py` → `class AngeeNode(strawberry.relay.Node):
  sqid: strawberry.relay.NodeID[str]`. relay's `resolve_id_attr` walks the MRO, so every subtype
  resolves by `qs.filter(sqid=...)` with NO custom resolver. Export `AngeeNode` + `Connection`
  from `base/graphql/__init__.py`.
- **Extensions in `build()` (Slice 1):** `extensions=[RebacExtension, *parts['extensions'],
  RebacDjangoOptimizerExtension]` — the **rebac** optimizer subclass (forces
  `prefetch_custom_queryset=True`; the plain one leaks via `_base_manager`), ordered LAST; no
  custom actor extension (ActorMiddleware owns the HTTP actor).
- **Read side:** `notes: Connection[NoteType] = strawberry_django.connection()`;
  `note: NoteType | None = strawberry_django.node()`; delete the hand-rolled `id()/sqid()/note(id)`;
  fix `word_count` → `@strawberry_django.field(only=['body'])`. **Nested connections** = a
  connection-typed field on the node type — the optimizer prefetches it window-partitioned +
  REBAC-scoped, no N+1, independent `first/after` per parent.
- **Boot invariant:** assert every GraphQL-exposed `RebacMixin` model's `_default_manager` is a
  `RebacManager` (else a connection is silently UNSCOPED — leak).
- **Spikes/forks (resolve at build):** (1) a private `NodeID` named `sqid` co-existing with an
  output field named `sqid` may be rejected by strawberry — fallback name `publicId`. (2) Keep
  `AngeeModel.public_id/from_public_id` as the canonical NON-GraphQL identity API (admin/resources/
  delete-preview); it already keys on sqid, so it stays consistent with the relay NodeID — one id
  story, not two. (3) Every queryable type must subclass `AngeeNode` ⇒ needs a `sqid`; under the
  GlobalID decision the per-model prefix is optional but recommended.
- **Risks:** extensions are the single load-bearing change (no optimizer ⇒ N+1 AND
  `DjangoCursorConnection` breaks on `ordering_descriptors is None`); keep `REBAC_STRICT_MODE=True`
  (False ⇒ actor-less context falls through UNSCOPED = silent leak); `SqidsField` supports only
  exact/in/gt/.../isnull lookups (fine — relay orders by Meta.ordering+tiebreaker, not sqid).

## Auth — investigation reaffirms "default `auth.User`"; see the FORK below
Investigation verdict: target is already half-wired for default `auth.User`
(`settings.py`: contrib.auth installed, `RebacBackend`+`ModelBackend`, `ActorMiddleware`,
`REBAC_STRICT_MODE=True`, default `AUTH_USER_MODEL`, `REBAC_ACTOR_RESOLVER` unset →
`default_resolver` maps `request.user`→`auth/user:<pk>`). Reconstruct: a **models-less**
addon `angee.accounts` (label `accounts`, NOT `auth`), `permissions.zed` (`auth/user`,
`auth/group`, `angee/role` admin member set), `graphql.py` exporting `schemas` with a
`@strawberry_django.type(get_user_model())` `UserType` + `Mutation.login`
(`authenticate`+`login` → session cookie) / `.logout` / `Query.currentUser`; register in both
app sets; seed demo users via `make_password`. DROP the custom `AbstractBaseUser`, the
`compat/` sys.modules shim, the hand-rolled backend/resolver, createsuperuser/changepassword,
Service/ApiKey/ImpersonationEvent, the Permission Hub.

### ✅ RESOLVED (evidence) — keep contrib.auth installed, swap only the User model
Audit of the installed stack: **`rebac` imports `from django.contrib.auth.models import
Group` (field_backing.py:144) and `AnonymousUser, Group` (actors.py:161) at runtime**, and
**`channels` imports `AnonymousUser`** (channels/auth.py) — directly, with NO
`get_group_model()`-style indirection. `simple_history`/`reversion`/`daphne` are clean
(get_user_model or nothing); `strawberry_django` only TYPE_CHECKING-imports. So **fully
removing `django.contrib.auth` would break rebac + channels** unless `django.contrib.auth.models`
is intercepted — which is exactly the `sys.modules` shim p1 used (its own docstring: the shim
exists "because we own the auth label … and django.contrib.auth is not installed", to satisfy
"contrib.admin and other contrib.auth-aware code"). That is the **forbidden monkey-patch** and
contradicts "prefer a library / no workarounds". → **DECISION: keep `django.contrib.auth`
installed (the framework rebac/channels depend on); make OUR user the `AUTH_USER_MODEL`** —
`AbstractBaseUser` + `rebac.RebacPermissionsMixin` (REBAC-pure: drops the inert
user_permissions/groups M2Ms) + the angee mixins (Timestamp + sqid → `public_id` → opaque
relay node id) + a `BaseUserManager`. That IS "our user replacing Django's", library-first,
**no shim**, and makes the User sqid-addressable for nested GraphQL. `pagination/sqid`
research (`wcrxlmdol`) confirms the relay-NodeID binding detail.

### ✅✅ FINAL (architect chose REBAC-native) — app = `angee.iam`
- **App:** `angee.iam` (label `iam`), `depends_on=("base",)`. Replaces the planned
  `angee.accounts`/`angee.auth` everywhere.
- **User:** an abstract Angee source model `User(AngeeModel-style with AbstractBaseUser +
  rebac.RebacPermissionsMixin + Timestamp + sqid)` emitted by the composer under label
  `iam`; `AUTH_USER_MODEL = "iam.User"` set in `compose_defaults` (both app sets). Carries a
  `sqid` → `public_id` so it is an opaque relay node. A `BaseUserManager`. **NO** Django
  `groups`/`user_permissions` M2Ms (RebacPermissionsMixin strips them).
- **Groups/roles/permissions:** owned by REBAC — defined in `iam/permissions.zed`
  (`auth/user`, `auth/group`, `angee/role` admin set). Authz = `RebacBackend` (single source).
  `ModelBackend` stays only for password credential checks.
- **contrib.auth:** stays installed (rebac/channels import its `Group`/`AnonymousUser`); its
  default `auth.User` is replaced by `iam.User` via the swappable setting. NO shim.
- **Verbs:** `iam/graphql.py` (→ `schema.py` per F5) exports `schemas` with `UserType` +
  `login`/`logout`/`currentUser` (session).
- **⚠ Build concern (verify carefully):** the custom User is an **Angee-composed swappable
  user** — `AUTH_USER_MODEL="iam.User"` must resolve to the emitted concrete
  `runtime/iam/models.py` in the RUN app set, be set BEFORE the first migration, and not
  break the BUILD (emit-only) app set. p1 did this (`AUTH_USER_MODEL="auth.User"` via a
  runtime-composed model) — mirror the clean wiring. Confirm app-loading order +
  makemigrations(iam) works. Flag if the composer needs a tweak to emit a swappable user.

### (historical) FORK — default user vs swappable custom user
The architect directed "replace Django's auth app (p1-style)"; the investigation says use the
default `auth.User`. p1's full replacement required a `sys.modules` monkey-patch (forbidden),
so it's out. The clean reconciliation that honors "replace the auth app" + "prefer library" +
"no monkey-patch" + the opaque-id need (the pagination/sqid research flags **default `User`
has no sqid**, so a default user is NOT addressable by opaque node id in nested GraphQL) is:
**a swappable custom `User` via Django-native `AUTH_USER_MODEL`, subclassing
`AbstractBaseUser`/`PermissionsMixin` + `rebac.RebacPermissionsMixin` + the angee mixins (so
it carries `sqid`/`public_id`), with `django.contrib.auth` KEPT installed for
Group/Permission/admin, and NO shim.** Working decision = this swappable custom user, PENDING
the pagination/sqid research (`wcrxlmdol`) confirming the sqid-on-User requirement. If that
research shows User need not be opaque-addressable, fall back to the investigation's default
`auth.User`. Surfaced to the architect.
