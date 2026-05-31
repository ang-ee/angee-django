# Auth + GraphQL Lift — durable state (compaction lifeline)

**This file is the source of truth for the in-flight program. Re-read it after any
compaction before acting.** Keep it updated as work lands.

## North-star goal (active Stop-hook condition)
Working `example/notes` **and** an `auth` addon **end-to-end**, strictly following
`AGENTS.md` + `docs/*` guidelines. No shortcuts, no workarounds, no codegen, step by
step clean code, **less code is better**, deferring to stack-owned libraries (rebac /
strawberry-django). Reconstruct, never copy; **no p1 baggage**; no provenance anywhere.

## The main thing (core intent — drives every milestone)
The point of this program is a **GraphQL contract change** and its propagation to the
client:
- **Pagination → standard GraphQL** (strawberry-django **relay connections**), replacing
  hand-rolled paging.
- **Aggregates / group-by work SERVER-SIDE** (via strawberry-django), not client-side.
- **Change how GraphQL is configured (server) AND consumed (client).**
- **Fix the SDK and the base UI** to consume the new standard pagination + the built
  queries.
So M1's read side must be relay-connection + server-side aggregates (no hand-rolled
paging/`NotesQuery`), and M3 rewrites the SDK + base UI to that contract. Keep "less
code is better": let strawberry-django own pagination/filtering/ordering/aggregation.

## Strategy (decided)
Three milestones, in sequence:
- **M1 — Auth + REBAC↔GraphQL lift** (current). Lift *just enough* from
  `../angee-django-p1` to make notes+auth work end-to-end on the new clean
  architecture. Scope: auth addon (User/Group/Service/ApiKey, password backend, login
  GraphQL verb, actor resolver → `REBAC_ACTOR_RESOLVER`); wire
  `rebac.graphql.strawberry.RebacExtension` + `rebac.graphql.strawberry_django.RebacDjangoOptimizerExtension`
  into `GraphQLSchemas.build()`; denial→GraphQL error formatter
  (`PermissionDenied`/`MissingActorError` → `UNAUTHENTICATED`/`PERMISSION_DENIED`);
  replace hand-rolled `AngeeGraphQLWSConsumer` with `rebac.graphql.strawberry.RebacChannelsConsumerMixin`;
  crud **read** side via strawberry-django (query/connection/node/filter/pagination/
  search) + opaque-id scalar; wire-or-delete `sync_permissions`; fix the example
  `manage.py test` graphql-module shadowing.
- **M2 — Dev stack / template.** Make `angee dev` work: build-watch, re-emit schema,
  makemigrations/migrate, load assets — via template hooks running these commands.
  Full GraphQL schemas; no UI/storybook yet. *"Make plans, let codex code."*
- **M3 — Frontend lift.** Fix the SDK and lift the p1 notes+auth frontend so login +
  full notes work end-to-end with standard GraphQL pagination and the built queries.
  Careful: no new bugs; record blockers, keep going in parallel.

## How we run the lift (the lift skill's automated workflow is DELETED/broken —
`.agents/workflows/lift.js` does not exist; commit `736fe3b Delete workflows`).
Orchestrate with our OWN `Workflow` runs, applying lift *principles*: reconstruct don't
copy, defer to the rebac/strawberry stack, land at the owning level, drop baggage.
Process: **understand → plan → review plan → codex builds on a workspace branch →
review build → land**. Do NOT corrupt the framework; build on the round-2-corrected base.

## In-flight right now (check status before acting)
- **Round-2 codex** — companion job `task-mpt7qzo8-hrcu9p`, codex session
  `019e7bd8-ce7d-7401-8dc6-a4efe95a3a84`. Fixing the 10 adversarially-verified
  round-2 findings (esp. the **critical xref label-alias canonicalization**). The lift
  must build on TOP of this. Status: `node <companion> status task-mpt7qzo8-hrcu9p`.
- **Understanding workflow** — `wm0rq9k5t` (run `wf_6052ae27-6a1`). Read-only; produces
  the minimal LIFT MAP (capability / lib_owned / reconstruct / drop_baggage /
  integration_seams / slices / risks / open_questions). When done, write the M1 plan
  from `map`, then proceed.
- Branch: `wip-base-lift-refactor`. Untracked `tests/test_instrument_tmp.py` is round-2
  codex scratch — leave it (codex owns it).

## Engines / paths
- codex companion: `/Users/alexis/.claude/plugins/cache/openai-codex/codex/1.0.4/scripts/codex-companion.mjs`
- `gemini`: /opt/homebrew/bin/gemini · `angee`: /usr/local/bin/angee
- Dispatch codex via the `codex:codex-rescue` subagent (Agent tool), not raw shell.
- p1 reference repo: `/Users/alexis/Work/fyltr/angee-django-p1` (read-only reference).

## Next concrete steps (autonomous)
1. When round-2 codex lands: re-run the gate myself (`ruff --no-cache`, `mypy`,
   `pytest`, `angee build --check`) **and a fresh/cleared-ledger example e2e** to
   confirm the xref label fix (the green unit gate missed the original regression).
2. Checkpoint commit.
3. When the understanding workflow lands: write `.agents/plans/lift-auth-graphql.md`
   from the LIFT MAP (minimal, clean-architecture, no baggage).
4. Quick adversarial plan review (workflow) → revise.
5. Build via codex on a workspace/branch (`angee ws create lift-auth-graphql` or a
   feature branch off the corrected base). Keep slices independently verifiable.
6. Adversarial code review (workflow) → fix loop → land.
7. Then M2, then M3.

## Understanding workflow: DONE (wm0rq9k5t). Map → plan written: `.agents/plans/lift-auth-graphql.md`.
Headline: most actor/REBAC plumbing is ALREADY WIRED or library-owned. M1 = wire rebac
lib classes + tiny error extension + login verb + read-side, and DELETE ~1600+ LOC of
p1 baggage. 7 slices (see the plan).

## ⚠ DIRECTIVE UPDATE (architect, supersedes F1): REPLACE Django's auth app
We are **replacing `django.contrib.auth`** with our own auth addon (custom user/group),
the way **p1 solved it with a compat shim** — NOT using the default `auth.User`. This
reverses F1's "use default auth.User / drop custom user / drop the shim."
- Study p1's mechanism (`compat/auth_models_shim.py` + `install.py`) — but p1 replaced
  `django.contrib.auth.models` in `sys.modules`, a **monkey-patch the constitution
  forbids**. Reconstruct the SAME capability cleanly: the Django-native swappable
  **`AUTH_USER_MODEL`** custom user (and our own Group/role surface) so consumers use
  `get_user_model()` and never `from django.contrib.auth.models import User` — no
  sys.modules patch. Confirm the exact clean shape from the investigation + p1's intent.
- Label-collision note flips: if `django.contrib.auth` is genuinely replaced/not installed,
  the addon CAN be label `auth`; if it stays installed (AUTH_USER_MODEL keeps contrib.auth
  for Group/Permission/admin machinery), keep a non-colliding label. Decide from the
  investigation (`wxafgtbrk` + the shim read).
- Aggregates fork RESOLVED: **use `../strawberry-django-aggregates`** (v0.2.2, py314; both
  p0/p1 depend on it) — add it as a dependency and wire it; do NOT hand-roll an ORM
  resolver. Reconstruct the capability WITHOUT p0/p1's codegen/emitter (`aggregates_emitter`,
  `runtime/aggregates.py`, Meta codegen hints) — we author the schema by hand.

## ⭐ STANDING PRINCIPLE (architect): ALWAYS PREFER A LIBRARY OVER BUILDING.
Before writing anything, find the library that owns it and wire it. For M1: Django's
swappable `AUTH_USER_MODEL` + `AbstractBaseUser`/`PermissionsMixin` for the user;
`rebac` (RebacPermissionsMixin/RebacBackend/actors/extensions/consumer-mixin) for authz;
`strawberry-django` for relay/filters/ordering; `strawberry-django-aggregates` for
aggregates; Django's own `authenticate`/`login` for credentials. We build ONLY the thin
seams the stack cannot own. Reject any hand-rolled equivalent of a library feature.

### p1 auth mechanism (mapped firsthand)
p1 set `AUTH_USER_MODEL = "auth.User"` where `auth` is **p1's OWN app** (`angee.auth`,
label `auth`); `django.contrib.auth` was **NOT installed**, and `compat/auth_models_shim.py`
+ `install.py` injected `django.contrib.auth.models` into `sys.modules` so third-party
imports kept working. That sys.modules injection is the **monkey-patch we must NOT
reproduce.** Clean equivalent (library-first): provide a custom user via the Django-native
swappable `AUTH_USER_MODEL` and make our own code use `get_user_model()` — no shim. **Open
design point (finalize from `wxafgtbrk`):** does contrib.auth stay installed (swap only the
User model — simplest, library-native) or get fully replaced (matches p1's REBAC-pure
intent but reintroduces the shim problem)? Lean: swap via `AUTH_USER_MODEL`, keep
contrib.auth for Group/Permission/admin machinery (which `RebacPermissionsMixin` neutralizes
anyway), unless the investigation shows a concrete blocker. If contrib.auth stays, the addon
label must not be `auth`; if it is genuinely replaced, label `auth` is free.

## Resolved forks (autonomous calls — review if you disagree)
- **F1 — auth model = DEFAULT `auth.User`.** Drop p1's custom User/Group/PermissionsMixin
  AND Service/ApiKey/ImpersonationEvent for M1. (Deviates from the original
  "User/Group/Service/ApiKey" brief, but matches "less code / no baggage / just enough";
  ApiKey/Service are product features with no role in notes+auth — defer to a later
  milestone if the product needs them.)
- **F2 — REBAC GraphQL extensions are UNIVERSAL in `base.GraphQLSchemas.build()`** (base
  imports `rebac.graphql.strawberry`; rebac is already a hard base dep). Not per-addon
  opt-in. Order is load-bearing: `[RebacExtension, RebacDjangoOptimizerExtension, DenialMapping]`.
- **F3 — session login (cookie) for M1** (contrib.sessions + AuthenticationMiddleware
  already wired). Token/bearer deferred.
- **F4 — denial codes: `UNAUTHENTICATED` + `PERMISSION_DENIED`** (Apollo standard + the
  user's item 3 spelling; p1 used NOT_AUTHENTICATED — deliberately diverge).
- **F5 — rename each addon GraphQL module `graphql.py` → `schema.py`** (one discovery
  hook in apps.py) to kill the graphql-core shadow so `manage.py test` works (item 11).
- **F6 — defer `currentUser.role_refs`** (not needed for M1).
- **F7 — delete the combined runtime `permissions.zed` emit** (compose/runtime.py:67);
  `rebac sync` reads each addon's own `.zed` — one owner per fact. Also delete the dead
  framework `sync_permissions` wrapper.
- **F8 — align `crud()` update to resolve by opaque public id** (like delete) for a
  consistent id surface (part of the user's opaque-id intent).

## Plan de-risked (DONE — wijc5050v, verified vs installed libs). Refinements folded into the plan:
- **F1 refined:** the addon package is **`angee.accounts`** (label `accounts`), NOT
  `angee.auth` — label `"auth"` collides with `django.contrib.auth` and Django refuses to
  start. Registered framework-wide in BOTH app sets via `compose_defaults`. REBAC types stay
  `auth/user`/`auth/group` (independent of the Django app label).
- **F4 refined:** denial mapping is a **`strawberry.Schema` subclass overriding
  `process_errors`** (SchemaExtension has no `process_errors` hook). `MissingActorError`→
  `UNAUTHENTICATED`, `PermissionDenied`→`PERMISSION_DENIED`.
- **F7 corrected:** there is no `sync_permissions`. Delete the combined `permissions.zed`
  emit (`runtime.py:67`), the now-dead `compose/rebac.py` (`render_permissions`/
  `write_permissions`) + its import (`runtime.py:17`), and the generated on-disk
  `runtime/permissions.zed`; re-run `angee build` so `build --check` stays green; reconcile
  `docs/backend/guidelines.md:133`.
- Use the **rebac** optimizer subclass (`rebac.graphql.strawberry_django.RebacDjangoOptimizerExtension`),
  NOT the plain strawberry_django one (the plain one leaks related rows via the unscoped
  `_base_manager`).
- **F8 refined:** relay node id must be the **sqid** (`relay.NodeID[str]`), and update-by-
  opaque-id needs `key_attr="sqid"` (or a custom resolver mirroring `_delete_resolver`).

## ⚠ FORK NEEDING YOUR CALL — Slice 7 aggregates owner
`strawberry-django 0.86` has **no native aggregation**; `docs/stack.md:32` names
`strawberry-django-aggregates`, which is **not installed/locked**. Options: (a) lock
`strawberry-django-aggregates` (a dependency-manifest + `docs/stack.md` change — you own
those) and wire it; or (b) a minimal server-side ORM-backed resolver
(`with_actor(actor)` BEFORE `.values().annotate()`), no new dep. **Default for autonomous
progress: (b)**, flagged here for reconciliation. Tell me if you want (a).

## Decisions & forks log (review later if needed)
- **D1 (locked):** resource ledger ownership inverted into the **composer** (base
  imports nothing from resources). [round-1]
- **D2 (locked):** **compose is build-only**; SDL render moved to a base `schema`
  command; explicit `ANGEE_BUILD` flag replaced the INSTALLED_APPS sniff. [round-1]
- **D3 (locked):** xref identity = `(source_addon, xref)`, exact match + index,
  fail-fast. [round-1] — **round-2 correction:** canonicalize the addon ref through the
  alias registry (name/label interchangeable) BEFORE the exact match; do NOT
  reintroduce `__endswith`.
- **D4 (strategic):** Stop hand-rolling the WS actor; **lift the rebac library mixin**
  (`RebacChannelsConsumerMixin`) instead. Round-2's hand-rolled WS fix will be
  superseded by M1.
- **FORK (open):** auth addon level — is `auth` a base addon (framework-owned) or a
  consumer addon? Default per guidelines: a base/framework addon so every project
  inherits it, but confirm against the LIFT MAP's `reconstruct[].level`. Record final
  choice here.
- **FORK (open):** opaque-id scalar — framework-level scalar on `AngeeModel.public_id`
  vs per-addon. Default: framework-level (DRY). Confirm from map.

## Build recipe (concrete, durable): `.agents/notes/lift-auth-graphql/RECIPE.md`
Aggregates = `strawberry-django-aggregates` **compute layer only** (compiler/operators/
granularity/errors), author types by hand, **eager REBAC scope (managed manager → actor →
compute_aggregation)**; never import its codegen layer. M1 aggregate surface = `count` +
group_by (word_count is a @property, not summable — promote later for sum/avg). **AUTH LOCKED
(architect):** app = **`angee.iam`** (label `iam`); `iam.User` = AbstractBaseUser +
`rebac.RebacPermissionsMixin` + angee mixins (sqid), emitted by the composer,
`AUTH_USER_MODEL="iam.User"`; groups/roles/permissions owned by **REBAC** (`iam/permissions.zed`,
`RebacBackend` single authz source, no Django permission M2Ms); contrib.auth stays installed
(rebac/channels need Group/AnonymousUser), no shim. Full design + the swappable-composed-user
build concern in RECIPE.md.

## Pointers (don't duplicate content)
- Findings + roadmap: `.agents/plans/2026-05-30-followups.md`
- Round-1 executed plan: `.agents/plans/2026-05-30-review-fixes.md`
- Round-2 brief (in flight): `.agents/plans/2026-05-30-review-fixes-round2.md`
- Reviewer personas: `.agents/agents/{architecture-reviewer,django-reviewer}.md`
- Six-pass reports: `.agents/notes/final-review/*.md`
- LIFT MAP + research reports: will be saved under `.agents/notes/lift-auth-graphql/`
  when the understanding workflow returns.
