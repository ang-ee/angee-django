# Angee core (`angee/*`) review — fix plan

> **STATUS: EXECUTED & VERIFIED (2026-06-05).** All 21 findings landed. Checks:
> 243 backend tests, 128 SDK tests, mypy + ruff clean, `angee build --check` and
> `schema --check` green, stale-runtime path yields a clean `CommandError`, CSRF
> test covers cookie-rejected / cookie+token / bearer-exempt. Decisions D1–D3
> implemented as chosen.

Consolidated from the six-reviewer pass (architecture + django, across `base/`,
`compose/`, `graphql/`). This plan covers every still-open finding and the
decisions needed before the cross-cutting ones can be implemented.

## Already resolved (by the deletion refactor)

- **Boundary:** `deletion.py` moved `base/` → `graphql/` — `DeletionPreview` is
  consumed only by GraphQL CRUD, so it now lives at its owning layer. ✅
- **Parallel representations:** the domain `DeletionPreview` + GraphQL
  `DeletePreview`/`from_domain` mirror types collapsed into one
  `@strawberry.type DeletePreview` (`has_blockers` now a property). ✅

Three deletion *internals* carried into the new file and are still open — see
G9–G11.

---

## Decisions (locked)

- **D1 — CSRF (G1): make it real (cookie + CSRF).** Add
  `django.middleware.csrf.CsrfViewMiddleware` to the IAM middleware fragment
  **and** mark bearer-authenticated requests CSRF-exempt
  (`request._dont_enforce_csrf_checks = True` in the actor/auth layer) so token
  clients keep working. Touches `addons/angee/iam`.
- **D2 — compose drift (C1/C2/C3): full ownership fix.** The AppConfig hook
  becomes **check-only**, the `angee build`/`clean` command owns emission, and
  the per-boot strict drift check is gated to build/CI (cheap sentinel/hash on
  ordinary boots). Fixes the `--check` traceback, removes the argv sniff, drops
  per-boot cost on prod workers.
- **D3 — `deleteGroup(confirm)` default (G8): flip to `False`.** First call is a
  non-destructive preview; deletion requires explicit `confirm: true`. **Requires
  a frontend sweep** of notes UI / SDK callers that rely on one-shot delete.

---

## Phase 1 — Security & correctness

### G1 — CSRF documented but not enforced  *(D1: make it real)*
`angee/graphql/views.py:39-47`, `angee/graphql/urls.py:10`; enforcement gap in
`addons/angee/iam/autoconfig.py:10`. The `csrf_token` endpoint issues a token but
the composed `MIDDLEWARE` (`CommonMiddleware` + Session/Auth/Actor/History/
Revision) installs no `CsrfViewMiddleware`, and Strawberry's view doesn't
self-enforce. **Implement:** (1) add `django.middleware.csrf.CsrfViewMiddleware`
to the IAM `MIDDLEWARE:append` fragment, positioned after `SessionMiddleware`
and before `AuthenticationMiddleware`; (2) in the actor/auth layer that resolves
bearer tokens, set `request._dont_enforce_csrf_checks = True` for
bearer-authenticated requests so the GraphQL POST isn't rejected for token
clients (this is the DRF `SessionAuthentication`-vs-`TokenAuthentication`
pattern). Verify: `CsrfViewMiddleware` present in rendered settings; a
cookie-auth mutation without `X-CSRFToken` is **rejected**; a bearer mutation and
a cookie mutation *with* the token both **pass**.

### C1/C2/C3 — compose drift: `--check` traceback, argv sniff, per-boot cost  *(decision D2)*
`angee/compose/apps.py:31-40` (the `sys.argv[1:3] in (...) and not sys.argv[3:]`
guard) ↔ `angee/compose/management/commands/angee.py:42-57`;
`runtime.py:160-186`. During `django.setup()` the hook runs `runtime.check()` for
every non-bare invocation; on stale runtime it raises → `ImproperlyConfigured`
traceback, pre-empting the command's clean `CommandError`. The argv sniff also
duplicates the `build`/`clean` names the command owns (the constitution's
inspect-a-global smell), and the full re-render runs on every boot incl. prod
ASGI workers. Implement per D2. **Add a regression test** whose fake
`Runtime.check()` raises on the `--check` path (current `tests/test_compose.py:248`
masks it because the fake never raises).

### B3 — `from_public_id` leaks `MissingActorError` outside actor scope
`angee/base/models.py:114-119, 130-140`. Under `REBAC_STRICT_MODE=True` with no
ambient actor, `cls._default_manager.filter(**lookup).first()` raises
`MissingActorError`, which is not in `except (TypeError, ValueError)`. The only
broken path is `resources load` (`addons/angee/resources/loader.py:310`,
`widgets.py:108`) — GraphQL callers always have an actor from `ActorMiddleware`.
**Owner-correct fix:** run the resources loader/command under `system_context`
(the loader is the caller that forgot to establish a system actor); do **not**
swallow `MissingActorError` in `from_public_id` (that would wrongly return `None`
for existing rows in system contexts). Verify with a strict-mode `resources load`
over a REBAC-typed resource and a no-actor unit test.

### B1/B2 — `json_safe` emits invalid / lossy JSON
`angee/base/serialization.py:14, 20-24`. (B1) non-finite floats (`nan`/`inf`)
pass the scalar branch and `json.dumps` emits bare `NaN`/`Infinity` — invalid
JSON on the subscription/resource wire; map via `math.isfinite` to `None` (or a
sentinel) before passing floats through. (B2) `set`/`frozenset` fall through to
`str(value)` (a non-deterministic repr — also violates byte-determinism) and
`bytes` becomes a repr string; add a `set | frozenset` branch (sort → recurse to
list) and a `bytes` policy (base64). Unit-test each value type.

### G2/G3 — `ChangePayload.from_instance` REBAC probe + relation leak
`angee/graphql/events.py:50-60`. (G2) replace `try: to_object_ref(instance)…
except TypeError` with the library's non-raising `model_resource_type(...)` probe
— the sibling `access.py:29` and the new `graphql/deletion.py:17` already use it.
(G3) `getattr(instance, field, None)` over `changed_fields` triggers a sync
related-object fetch on the save path when `update_fields` names a relation, and
`json_safe` broadcasts the related object's `__str__` to every subscriber,
unredacted. Read concrete local fields via `field.attname` (raw FK id) and skip
non-concrete fields so the publisher stays query-free and leak-free. Test with a
`save(update_fields=["<relation>"])`.

### G8 — `deleteGroup(confirm)` destructive default  *(D3: flip to False)*
`angee/graphql/crud.py:119` (`def delete(id, confirm: bool = True)`). Flip the
default to `False` so a bare `deleteGroup(id)` returns a preview without
deleting; deletion requires `confirm: true`. **Coupled frontend sweep:** find
every `deleteGroup`/delete-mutation caller in the SDK and notes/console web
(`packages/`, `examples/notes-angee/web`) and pass `confirm: true` where a real
delete is intended; regenerate any GraphQL codegen. Ship backend + frontend
together — this is a contract change. Verify: a no-`confirm` call leaves the row
intact and returns the preview; an explicit `confirm: true` deletes when
unblocked.

---

## Phase 2 — Structural / find-the-owner

### B4 — public-id type-switching → polymorphism on `SqidMixin`
`angee/base/models.py:121-127` (`public_id_lookup`), `165-170`
(`_public_id_value`). Both branch on `SqidMixin` from outside. Give `SqidMixin`
the hooks (instance public-id value + classmethod lookup), default to pk on the
base, override in `SqidMixin`; delete the `issubclass`/`isinstance` branches.

### G9 — `DeletePreview.from_instance` parallel inventory
`angee/graphql/deletion.py:142-178`. The tree (`from_target`→`_PreviewRows.by_model`)
and the flat `deleted` groups (`deleted_counts`) walk `collector.data` +
`fast_deletes` twice. Derive the flat groups from the already-built `_PreviewRows`
map (it carries `total_count` per model) so per-model counts are produced once.

### G4 — `AngeeSchema` lives in `errors.py`
`angee/graphql/errors.py:12`. It's the central `strawberry.Schema` subclass, not
an error helper. Move it into `schema.py` (or rename to reflect it owns REBAC
denial codes); update the import at `schema.py:172`.

### C5/C6 — path coercion DRY + private naming
`composer.py:72` (`path_value`, public-named but internal), `:81`
(`set_composer_setting`), `defaults.py:12` (`_path_value`), `settings.py:58,130,138`,
`asgi.py:24` — same `expanduser().resolve()` idiom in 4 modules, two variants
disagreeing on the non-path error. Extract one `resolve_path` helper for the
compose package, call it everywhere, and underscore the two internal `Composer`
helpers (only `compose_settings` is public).

### C4 — addon discovery duck-types on `hasattr(depends_on)`
`angee/urls.py:16`, `angee/asgi.py:52`. Any third-party `AppConfig` with a
`depends_on` attr + a conventional `urls.py`/`asgi.py` is silently pulled in.
Discriminate on an explicit framework-owned marker instead.

---

## Phase 3 — Cleanups & nits

- **G5** delete unused `GraphQLSchemas.from_addons` forwarding alias
  (`schema.py:130-137`); have tests call the constructor.
- **G6** derive `_ROOT_TYPE_NAMES` from the single `SCHEMA_PART_KEYS` inventory
  (`schema.py:33-46`, `88-97`) so a bucket is declared once.
- **G7** give schema-build caching one owner on `GraphQLSchemas` instead of an
  unbounded `lru_cache` on one of three serving entrypoints (`views.py:18` vs
  `asgi.py:16` vs `management/commands/schema.py:71`).
- **B5** `RevisionMixin.revert_to` (`mixins.py:144-151`): pass
  `update_fields=[…reverted…]` to `save()` so it can't flush unrelated stale
  columns; decide whether the revert should itself be recorded as a revision.
- **G10** document that `DeletePreview.from_instance` should run in a transaction
  (the GraphQL delete path already wraps it) so the multi-query counts in
  `from_fast_delete` (`deletion.py:273-278`) are snapshot-consistent for other
  callers.
- **G11** if a REBAC resource-typed model is guaranteed a REBAC manager, call
  `with_actor`/`with_action` directly in `_read_scoped_queryset`
  (`deletion.py:325-331`) and drop the `getattr`/`callable` guards; else add a
  one-line comment naming the case they protect.
- **C7** emit `app_label = {label!r}` (`runtime.py:283`) for consistency with the
  other `repr()`-emitted Meta values and to remove the latent quoting assumption.
- **C8** tighten `_ensure_cleanable` (`runtime.py:499-518`) to require the
  generated sentinel `__init__.py` (or empty dir) as the sole authorization to
  clean; treat "only migrations, no sentinel" as not-an-Angee-runtime → refuse.
- **C9** `_handle_build` (`angee.py:50-53`): drop the redundant post-emit
  `check()` on the freshly-written path, or add a one-line comment if it's a
  deliberate write-integrity gate.
- **C10** *(architect judgment, likely leave as-is)* per-addon source-model
  introspection in `Runtime.model_contributions` (`runtime.py:648`) vs moving it
  onto the addon `AppConfig` owner — blocked by the no-shared-base-config
  constraint; document the decision rather than churn it.

---

## Verification (per the backend Checks)

After each phase, from the repo root:

```sh
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py angee build --check     # must give clean CommandError when stale
uv run python -m pytest tests/                                 # full framework suite
uv run python -m mypy angee/
uv run examples/notes-angee/manage.py schema --check
```

Plus targeted: subscription/CRUD tests for events + deletion changes; a
strict-mode `resources load` for B3; a cookie-vs-bearer mutation check for G1.

## Notes / non-findings (do not "fix")

- `except TypeError, ValueError:` in `models.py` is valid Python 3.14 (PEP 758,
  parenthesis-free exception tuples) — not a bug.
- The "select_related a REBAC-guarded relation in an actor-scoped resolver" trap
  does **not** occur in `angee/graphql/` (the one guarded `select_related` runs
  under `system_context`).
- REBAC write authorization is signal-enforced and solid; SDL emission is
  deterministic.
