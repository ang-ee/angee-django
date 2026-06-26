# REBAC vs GraphQL `permission_classes`: redundant or load-bearing?

Date: 2026-06-23

Scope:

- `../django-zed-rebac/src/rebac/` (sibling checkout) — the authorization owner.
- `angee/base/models.py`, `angee/graphql/crud.py`, `angee/graphql/data/queries.py`.
- `strawberry_django/{mutations/resolvers,permissions}.py` (the `.venv` in
  `../angee-poc-refine`).
- Usage split: notes (`crud(...)` with NO `permission_classes`) vs integrate
  (`_ADMIN_PERMISSION_CLASSES` + `write_context`).

## Executive Read

For a **standard REBAC-typed model** exposed through the generic `crud` / `data_query`
surfaces, authorization for read/update/delete is **already enforced automatically by
`rebac` at the manager/queryset/signal layer**. The actor is ambient
(`rebac.middleware.ActorMiddleware`, wired in `iam/autoconfig.py`), so every queryset
materialisation and every `.save()`/`.delete()` is gated without any GraphQL-layer help.

Create is also gated by the rebac `pre_save` signal — including the hard case
(`create = admin->member` const arrows and `create = authenticated` built-ins), which
the signal evaluates against an empty resource id and **resolves correctly**. The one
gap the signal genuinely cannot evaluate is a per-row `create` that depends on
*stored* relations the new (id-less) row would carry (e.g. `create = vault->write`);
that is the niche `AngeeManager.check_create` (`rebac.check_new`) exists for — and it is
used by **custom manager factories** (knowledge), **not** by the generic `crud`.

Therefore: `permission_classes` is **redundant as row authorization** for standard
models. Where it is used today (integrate/agents admin consoles) it is **not** doing
row authorization at all — it is the **admin-console entry gate paired with
`write_context` (`system_context`) which deliberately *bypasses* the rebac signal**.
That combination is load-bearing *as a pair*, but it is a chosen pattern, not a
necessity: the same const-admin `create` gate the signal already understands could carry
those writes with no GraphQL gate. See the discrepancy note below.

**Verdict per surface**

| Surface | Read | Create | Update | Delete | `permission_classes` is… |
|---|---|---|---|---|---|
| notes (standard, no perm classes) | rebac queryset scope | rebac `pre_save` (`authenticated`) | rebac `pre_save` `write` | rebac `pre_delete` | not present — not needed |
| integrate/agents admin (perm classes + `write_context`) | rebac scope + admin gate | `system_context` bypass, gated by perm class | `system_context` bypass, gated | `system_context` bypass, gated | load-bearing **only because** the write is elevated past the signal |

## Owner map (authorization)

- **Read scope (list/detail/aggregate/count/exists):** `RebacQuerySet` —
  `_apply_scope_in_place` injects `<id>__in=accessible()` on
  `_fetch_all`/`iterator`/`count`/`exists`/`aggregate`
  (`managers.py:199`, `:336`, `:435`, `:441`, `:447`). Field redaction:
  `_fetch_all` → `apply_field_visibility` (`managers.py:348`).
- **Bulk update/delete:** `RebacQuerySet.update`/`delete` →
  `_guard_bulk_action` (all-or-nothing) (`managers.py:462`, `:476`, `:485`).
- **Per-row save/delete (the path generic CRUD takes):** `rebac.signals`
  `_rebac_pre_save` / `_rebac_pre_delete` — `check_access(action="create"|"write"|"delete")`
  on every `RebacMixin.save()`/`.delete()` (`signals.py:54`, `:88-105`, `:127`, `:147`).
- **Actor:** ambient `current_actor()` ContextVar populated by
  `rebac.middleware.ActorMiddleware` (wired `addons/angee/iam/autoconfig.py:11`).
  Per-instance `_rebac_actor` (stamped by `from_db`/queryset) takes priority
  (`signals.py:78`, `:137`).
- **Strict default:** no actor + `REBAC_STRICT_MODE` → `MissingActorError`
  (`signals.py:80`, `managers.py:188`) — fail-closed.
- **Id-less create preflight (the gap):** `AngeeManager.check_create` →
  `rebac.check_new` evaluates the `create` permission against the relations the new row
  *would* carry (`angee/base/models.py:79-115`, `preflight.py:71`).
- **GraphQL gate (`permission_classes`):** `strawberry_django.permissions` —
  `BasePermission.has_permission` runs *before* the resolver; the Angee admin gate is
  `PlatformAdminPermission` (`addons/angee/iam/permissions.py:48-72`).

## Evidence

### 1. Read is automatic at the rebac layer

`data_query` builds list/detail over the model's default manager
(`angee/graphql/data/queries.py:92` `strawberry_django.offset_paginated(...)`,
`:102` `detail(...)`). The default manager is `AngeeManager`/`RebacManager`, so any
materialisation routes through `RebacQuerySet._fetch_all` → `_apply_scope_in_place`
(`managers.py:336`, `:199`) and `count`/`exists`/`aggregate` (`:435`/`:441`/`:447`).
The notes read surface (`NotesQuery`) carries **no** `permission_classes`
(`examples/notes-angee/addons/example/notes/schema.py:115-128`) and is still
row-scoped. Confirmed: read needs no GraphQL gate for standard models.

### 2. Update / delete are automatic at the rebac layer

Generic update: `_AngeeUpdateMutation.instance_level_update` →
`mutation_resolvers.update` → `instance.save()`
(`crud.py:177`; resolvers `update` `:540`). `save()` fires `_rebac_pre_save`, which runs
`check_access(action="write")` and per-field `write__<f>` gates (`signals.py:88-124`).
Generic delete: `_delete_resolver` → `delete_by_public_id` → `instance.delete()` →
`_rebac_pre_delete` runs `check_access(action="delete")` (`signals.py:127-150`).
Notes update/delete (`crud(NoteType, update=NotePatch, delete=True)` with no perm
classes, `schema.py:133`) are fully authorized by rebac alone.

### 3. Create through generic CRUD — the signal, NOT `check_create`

`_AngeeCreateMutation.create` → `mutation_resolvers.create` →
`manager.create(**kwargs)` → `Model.save()` (`crud.py:138-147`; resolvers `_create`
`:459`). It does **not** call `AngeeManager.check_create`. Grep confirms `check_create`
has only three callers, all in `addons/angee/knowledge/models.py` (`:54`, `:120`,
`:223`) — custom manager factories, not the generic surface.

So generic create is gated **only** by the `pre_save` signal, which on insert sets
`resource_id=""` (`signals.py:97-98`) and runs `check_access(action="create", ...)`.
The empty-id branch of `LocalBackend._check_access` (`backends/local.py:303-344`)
evaluates the `create` permission against an empty row, and only falls back to
"is `accessible()` non-empty?" if that fails. This means:

- `create = authenticated` (notes, `permissions.zed:22`): `builtin_actor_matches`
  grants any non-anonymous subject (`schema/walker.py:263-268`). The signal grants it
  with no overlay. **Notes create is fully authorized by rebac alone, no perm class.**
  Proven end-to-end by `django-zed-rebac/tests/test_create_gate.py:156-173`.
- `create = vault->write` (knowledge): the new id-less row has no stored `vault`
  relation, so the signal cannot evaluate it. This is the real gap → `check_create`
  / `check_new` with the relation overlay (`models.py:104-114`, `preflight.py`),
  called from the knowledge manager factory. **Not the generic `crud` path.**
- `create = admin->member` (integrate, `permissions.zed:13`): `admin` is
  `rebac:const=admin`. `_walk_const_arrow` resolves the const target
  (`angee/role:admin#member`) **independent of the resource id**
  (`backends/local.py:825-846` — note: no `resource_id` parameter). So the empty-id
  create check **GRANTS a real platform admin**. Proven by
  `tests/test_create_gate.py:109-125`
  (`test_create_const_admin_grants_member_of_const_role`).

### 4. What the admin surfaces use `permission_classes` for

`_ADMIN_PERMISSION_CLASSES = [PlatformAdminPermission]`
(`addons/angee/iam/permissions.py:72`). `PlatformAdminPermission.has_permission`
(`:54-69`) checks platform-admin reach by `user_model.objects.filter(pk=user.pk).exists()`
— and because `auth/user` has `read = admin->member` (`iam/permissions.zed`),
that `.exists()` is itself REBAC-scoped: it returns True only for an actor with
`angee/role:admin#member`. So the gate is **rebac-backed admin reach, expressed at the
GraphQL boundary**, not an independent rule.

Crucially, integrate's `crud(...)` pairs the gate with `write_context=...`
(`integrate/schema.py:1086`, `:1533`, `:1561`, `:1572`), which attaches
`_SystemContextWrite` — it runs the write inside `system_context(reason=...)`
(`crud.py:30-50`). `system_context` sets the sudo ContextVar, so `_rebac_pre_save`
**bypasses** the create check entirely (`signals.py:74`, `actors.py:352-361`). With the
signal bypassed, **`permission_classes` is the only authorization left** for those
writes — hence load-bearing *as a pair with `write_context`*.

### 5. The integrate `.zed` comments are inaccurate (a real discrepancy)

`integrate/permissions.zed:9-12`, `:43-45`, etc. claim the const-admin `create` check
"sees an empty resource id and denies everyone", justifying the
`system_context`+`permission_classes` workaround. The rebac engine and its tests
(`test_create_gate.py:109-121`) show the opposite: a const-arrow `create` **resolves
for a genuine admin with an empty id**. So those admin creates do **not** need to be
elevated past the signal to succeed for an admin; the signal would authorize them.
The `write_context`/`permission_classes` pair is a chosen redundancy (and double
source of truth), not a forced one — it predates / misreads the empty-id const
behaviour. (Out of scope to change here; flagged for follow-up.)

## Answers

1. **Where enforced, per op (standard model, generic surfaces):**
   read = `RebacQuerySet` scope (`managers.py:199/336/435/441/447`), automatic;
   update = `_rebac_pre_save` `write` (`signals.py:88-124`), automatic;
   delete = `_rebac_pre_delete` `delete` (`signals.py:127-150`), automatic;
   create = `_rebac_pre_save` `create` with empty id (`signals.py:97-105` →
   `backends/local.py:303-344`), automatic — sufficient for `authenticated` and
   const-`admin->member`; the only un-evaluatable case is `create` depending on the
   new row's *stored* relations, handled by `check_create`/`check_new` in custom
   factories.

2. **Is `permission_classes` redundant for standard models?** Yes — as row
   authorization it is double work. The notes path proves it: create/update/delete are
   fully authorized by rebac alone with no `permission_classes`. Generic create is *not*
   merely "authenticated-only" — the per-type `create` expression is enforced by the
   signal (notes' `authenticated`, integrate's const `admin->member` both resolve).

3. **What admin surfaces use it for:** the platform-admin **entry gate**, paired with
   `write_context`/`system_context` that **bypasses** the rebac signal. It is the only
   guard *given that the write is elevated*. It is not expressing something rebac
   can't — rebac already expresses `create = admin->member` and resolves it for the
   empty-id create (§3, §5). The "gap" is self-inflicted by the elevation. Pushing it
   fully into rebac is already possible: drop `write_context`, let the signal's const
   create check gate the write; the GraphQL gate then needs nothing.

4. **Recommendation for the nestjs write surface:** **do not add
   `permission_classes`.** Route writes through the rebac manager and the scoped write
   queryset (`_write_queryset`, `crud.py:223`) with the request actor ambient, and let
   the `pre_save`/`pre_delete` signals authorize per row. `permission_classes` is
   genuinely load-bearing **only** when a write is intentionally run under
   `system_context` (the admin-console elevation pattern). If the nestjs surface does
   not elevate writes (it should not, for standard models), it needs no GraphQL/route
   gate — the rebac signal + queryset scope is correct and complete. The single case to
   carry deliberately is id-less `create` that depends on the new row's stored relations
   (`create = vault->write`): mirror the knowledge factory — call `check_new` with the
   relation overlay, then insert under per-instance sudo and re-bind the actor.

5. **Risk of REMOVING `permission_classes` where it exists today:** **Yes, real** — but
   only because of the `write_context` pairing. Removing the perm class while keeping
   `write_context` (system_context) would leave admin creates/updates/deletes with **no
   authorization at all** (signal bypassed, gate removed) — under-protected. Safe removal
   requires removing `write_context` in the same change so the rebac signal re-engages
   (const `admin->member` then gates create for admins; non-admins are denied). For read
   surfaces, the admin `permission_classes` additionally hides the *existence* of rows a
   non-admin's read scope would already empty out; removing it would not leak rows (scope
   still empties them) but would change a hard 403 into a silent empty list. Treat as a
   product decision, not a security regression, for reads.

## Bottom line

- Standard models: rebac is the single source of truth for CRUD authz; GraphQL
  `permission_classes` is redundant. The nestjs write surface should rely on the rebac
  manager + scoped write queryset and carry **no** `permission_classes`.
- Admin consoles: `permission_classes` is load-bearing *only* because `write_context`
  elevates the write past the signal. That pairing is a choice; the underlying const
  `create` gate is already rebac-expressible and resolves on empty-id create
  (`test_create_gate.py:109-121`), so the redundancy can be retired.
