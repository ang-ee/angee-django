# Generic CRUD: what moves upstream, what stays Angee glue

Date: 2026-06-23

> **Decision (2026-06-23, architect):** Moves #1–#4 are **done** (Phase 0, verified;
> Angee `crud.py`/`ids.py` −121 lines). **Move #5 is DECLINED** —
> `DeletePreview`/`delete_by_public_id` **stay in Angee**, not relocated to rebac
> (avoids importing the sqid identity into rebac + a risky typed-surface move). The
> "## Net shape" / Move #5 rows below are superseded on that point: `deletion.py`
> remains Angee-owned (its delete resolver now sources the write target via the
> rebac `for_write()` convenience). Move #6 remains open.

Scope (read-only research; only this note written):

- Subject: `angee/graphql/crud.py`, `angee/graphql/deletion.py`,
  `angee/graphql/ids.py`, with `angee/graphql/node.py` and
  `angee/graphql/data/queries.py` for context.
- Upstream owners (all Angee-owned forks/libs — moving code in is low-risk):
  - `strawberry-django` fork — `/Users/alexis/Work/angee/strawberry-django`
    (branch `codex/input-object-extensions`).
  - `strawberry-django-nestjs` — `/Users/alexis/Work/angee/strawberry-django-nestjs`
    (`github.com/ang-ee/strawberry-django-nestjs`, confirmed Angee).
  - `strawberry-django-aggregates` — `/Users/alexis/Work/angee/strawberry-django-aggregates`.
  - `django-zed-rebac` — `/Users/alexis/Work/angee/django-zed-rebac`
    (**confirmed Angee**: remote `git@github.com:ang-ee/django-zed-rebac.git`,
    `pyproject.toml` author "Angee, Inc.", v0.11.1).
- Cross-checked against `.agents/notes/rebac-graphql-permission-classes.md` and
  `.agents/notes/data-management-library-research.md`.

## Executive Read

`crud.py` is mostly **compensation for one upstream bug** plus **two thin REBAC
conveniences**, with a small irreducible Angee core. The single highest-value,
safest move is fixing the `key_attr` lookup gap in the strawberry-django fork:
the library reads the relation/PK input under the configured key
(`DEFAULT_PK_FIELD_NAME` = `"sqid"`) but then always queries by **`pk=`**
(`resolvers.py:82`, `:86`). Closing that one gap deletes `coerce_relation_public_ids`,
`_AngeeCreateMutation`, `_resolve_for_write`'s custom branch, and most of
`_AngeeUpdateMutation.instance_level_update` — i.e. Angee stops subclassing the
mutation fields at all and goes back to plain `strawberry_django.mutations.create/update`.

A second, independent fork fix — copy `key_attr`/`argument_name` in
`DjangoMutationCUD.__copy__` (`fields.py:213-217`) — deletes `_AngeeMutationCloneMixin`
outright. It is a real upstream defect: the field's own `__init__` sets both as
instance state but `__copy__` drops them.

`_write_queryset` (`on_field_deny("allow")` = row scope, redaction off for write
targets) is a REBAC concern and belongs as a `RebacQuerySet.for_write()`
convenience in `django-zed-rebac` (which already owns `with_actor`/`with_action`/
`on_field_deny` and has a `rebac/graphql/strawberry_django.py` glue module).

`DeletePreview`/`delete_by_public_id` is a **generic cascade-preview capability**
with one REBAC dependency (read-scoped visible rows). It is not Angee-specific and
not strawberry-django-specific; its best home is **`django-zed-rebac`'s strawberry
glue module** (it already imports `rebac` for actor scoping and resource types),
exposed as a reusable preview type + resolver. Only the sqid display label
(`public_id_of`) and the `PublicID`/`require_instance_for_id` addressing stay Angee.

The nestjs **envelope builder** (`createOneX(input:{x:{…}})` → model kwargs) is the
one piece that belongs in `strawberry-django-nestjs`, but Angee's `crud()` does **not**
emit the nestjs shape — it emits flat `create<model>/update<model>/delete<model>`
fields. So this is a *future* home for the nestjs write surface, not a move of
existing `crud.py` code. `strawberry-django-nestjs` today owns only `input_to_dict`
(`mutations.py:17`); a full create/update/delete envelope builder is the gap.

Per the permission-classes note: `permission_classes`/`write_context`/`_SystemContextWrite`
is a **removable redundancy**, not an upstream candidate — drop it where it pairs
with `write_context` (admin consoles) and let the REBAC signal gate the const-admin
create. That deletion lands *in Angee*, not upstream.

Net: `crud.py` shrinks from ~270 lines to a thin (~40-line) factory that builds
flat create/update/delete fields over the **stock** `strawberry_django.mutations`
fields and a rebac-owned delete resolver — no mutation subclasses, no relation
coercion, no `_write_queryset`, no `_resolve_for_write`.

## Owner map (each generic piece)

| Piece (`crud.py`/`deletion.py`/`ids.py`) | Owner it belongs to |
|---|---|
| `coerce_relation_public_ids` (`ids.py:57`) | **strawberry-django** (upstream-bug-fix: `key_attr` lookup) |
| `_AngeeCreateMutation.create` (`crud.py:138`) | **strawberry-django** (deleted by the same fix) |
| `_AngeeUpdateMutation.instance_level_update` (`crud.py:153`) | **strawberry-django** (mostly deleted by the same fix) + rebac `for_write()` |
| `_resolve_for_write` (`crud.py:233`) | **strawberry-django** (`get_with_perms` already does this; the custom branch is the bug-fix) |
| `_AngeeMutationCloneMixin` (`crud.py:125`) | **strawberry-django** (upstream-bug-fix: `__copy__` drops `key_attr`/`argument_name`) |
| `_write_queryset` (`crud.py:223`) | **django-zed-rebac** (`RebacQuerySet.for_write()`) |
| `DeletePreview` + tree + `delete_by_public_id` (`deletion.py`) | **django-zed-rebac** strawberry glue (generic cascade preview; read-scope is rebac's) |
| `_read_scoped_queryset`/`_requires_read_scope` (`deletion.py:353`,`:374`) | **django-zed-rebac** (pure rebac queryset/resource-type logic) |
| `_SystemContextWrite` + `write_context` + `permission_classes` (`crud.py:30`,`:53`) | **stays/deleted in Angee** (removable redundancy — see permission-classes note; not upstream) |
| `crud()` factory shell (`crud.py:53`) | **stays Angee glue** (Angee schema naming convention: flat `verb<model>` fields) |
| `PublicID`, `instance_for_id`, `require_instance_for_id` (`ids.py:17-54`) | **stays Angee glue** (sqid ⇄ GraphQL ID boundary) |
| `assert_unique_sqid_prefixes` (`ids.py:82`) | **stays Angee glue** (sqid prefix invariant) |
| nestjs create/update/delete envelope builder (does not exist in `crud.py`) | **strawberry-django-nestjs** (future nestjs write surface) |

## Evidence

### 1. The `key_attr` lookup bug — folds relation coercion + write-resolve upstream

`coerce_relation_public_ids` exists because strawberry-django resolves relation
inputs by **`pk=`**, never by the configured key:

- `_parse_pk` reads the related object's key from the input under `key_attr`
  (`resolvers.py:79-81`) but then queries `model._default_manager.get(pk=obj_pk)`
  (`resolvers.py:82`) — and the bare-scalar branch is `get(pk=value)`
  (`resolvers.py:86`). `key_attr` selects the *input field name*, not the
  *lookup column*.
- `prepare_create_update` only treats a FK value as something to resolve when it
  is `(ParsedObject, str)` and routes it through `_parse_data` → `_parse_pk`
  (`resolvers.py:300-311`). A plain `str` sqid therefore becomes `get(pk="ven_…")`
  → wrong/`DoesNotExist`.
- Angee's own setting makes the boundary key `sqid`: `PUBLIC_ID_FIELD_NAME = "sqid"`
  (`angee/graphql/constants.py:5`), wired as `DEFAULT_PK_FIELD_NAME`
  (`angee/graphql/autoconfig.py:10`). So the input arrives as sqid but upstream
  queries by raw pk.
- `coerce_relation_public_ids` (`ids.py:57-79`) walks FK/M2M fields and replaces
  string ids with **model instances** via `require_instance_for_id`
  (`ids.py:115`), because `_parse_pk` *does* accept a `models.Model` and passes it
  through untouched (`resolvers.py:68-69`). Angee is pre-resolving exactly the
  values upstream mis-resolves.

The fix: in `_parse_pk` (and the `prepare_create_update` FK branch), when
`key_attr` is not the model's pk, look up by `{key_attr: value}` instead of `pk=`.
With `DEFAULT_PK_FIELD_NAME="pk"` (upstream default, `settings.py:66`) behaviour is
unchanged; with `"sqid"` it resolves sqids natively. This is the **single change
that lets Angee drop `coerce_relation_public_ids` and stop overriding `create`**.

Consequences once fixed:

- `_AngeeCreateMutation.create` (`crud.py:138-147`) becomes identical to
  `DjangoCreateMutation.create` (`fields.py:301-311`) — delete the subclass; use
  the stock create field.
- `_AngeeUpdateMutation.instance_level_update` (`crud.py:153-181`) is a near-copy
  of `DjangoUpdateMutation.instance_level_update` (`fields.py:357-389`); the only
  deltas are (a) the `coerce_relation_public_ids` wrap (gone with the fix) and
  (b) `_resolve_for_write` instead of `get_with_perms`. `get_with_perms`
  (`fields.py:370`) already does pk/`key_attr` lookup with perms; the bespoke
  `_resolve_for_write` (`crud.py:233-252`) re-implements that lookup only to apply
  `_write_queryset` (the redaction-off scope). Folding the `key_attr` lookup
  upstream removes Angee's reason to re-derive `pk` and re-resolve here — what
  remains (redaction-off write scope) is the rebac `for_write()` queryset (§3).

Safety: high. Angee owns the fork; default-pk behaviour is preserved; the change
makes the documented `DEFAULT_PK_FIELD_NAME` contract actually apply to relation
lookups (today it half-applies — see `data-management-library-research.md` which
flagged the same "lookup field is whatever DEFAULT_PK_FIELD_NAME says" gap on the
*filter* side; this is the *mutation* side of the same gap). Behaviour for sqid
inputs is unchanged (Angee already resolves them to instances); only the owner of
the resolution moves from Angee to the library.

### 2. `__copy__` drops `key_attr`/`argument_name` — deletes `_AngeeMutationCloneMixin`

`DjangoMutationCUD.__init__` stores both as instance state: `self.key_attr`
(`fields.py:204`) and `self.argument_name` (`fields.py:209`). But
`DjangoMutationCUD.__copy__` copies only `input_type` and `full_clean`
(`fields.py:213-217`) — confirmed verbatim. Strawberry clones fields when a type
is reused, so a copied update field silently reverts `key_attr` to the settings
default and `argument_name` likewise. `_AngeeMutationCloneMixin.__copy__`
(`crud.py:125-132`) re-applies exactly those two attributes.

The fix lives in the field that owns the state: add
`new_field.key_attr = self.key_attr` and
`new_field.argument_name = self.argument_name` to
`DjangoMutationCUD.__copy__`. Then `_AngeeMutationCloneMixin` deletes entirely.
Safety: high, behaviour-preserving (it restores attributes the field already
declared). This is a genuine upstream defect independent of Angee, worth fixing
even apart from the deletion it unlocks.

### 3. `_write_queryset` → `RebacQuerySet.for_write()`

`_write_queryset` (`crud.py:223-230`) takes the default manager's queryset and,
if it has `on_field_deny`, calls `on_field_deny("allow")` — i.e. keep row scope,
turn off field **read** redaction (you cannot write through a redacted field).
`on_field_deny` is a `RebacQuerySet` method (`managers.py:100-106`); the whole
concept (`_rebac_field_deny`, `effective_field_deny_mode`, redaction in
`_fetch_all`) lives in `django-zed-rebac`. The `getattr(..., "on_field_deny")`
guard in `crud.py:227` is Angee probing for a rebac queryset from outside — the
exact "function that inspects an object to decide" smell.

Move it to a named rebac convenience, e.g. `RebacQuerySet.for_write()` returning
`self.on_field_deny("allow")` (and a manager passthrough like the existing ones at
`managers.py:616-617`). Angee then calls `model._default_manager.for_write()` with
no `getattr` probe; non-rebac models simply won't have the method, which is the
point (only rebac models need it). Safety: high; pure relocation of a rebac fact
to its owner. Unlocks deleting `_write_queryset` and the duck-typed guard.

### 4. `DeletePreview` / `delete_by_public_id` — generic cascade preview, best home is rebac's strawberry glue

`deletion.py` is built on **Django's own** `Collector` (`deletion.py:146-178`):
forecast counts of deleted/updated/blocked rows plus a capped, ordered preview
tree. Nothing in the core is Angee- or strawberry-django-specific — it is a
reusable "what would `delete()` cascade?" capability. Its only non-Django
dependencies are REBAC: `_read_scoped_queryset` filters visible rows by
`with_actor(actor).with_action("read")` (`deletion.py:353-371`) and
`_requires_read_scope` keys off `model_resource_type` (`deletion.py:374-377`) —
both already `rebac`-owned (`from rebac import …`, `deletion.py:17-18`).

Placement options weighed:

- **strawberry-django** — wrong: it has no REBAC concept; the read-scoped visible
  rows are intrinsic to the value (they prevent leaking row labels a non-admin
  can't read), so a rebac-free version would be a lesser capability.
- **django-zed-rebac strawberry glue (`rebac/graphql/strawberry_django.py`)** —
  best fit. That module already exists, already owns rebac↔strawberry-django glue,
  already imports `model_resource_type`/`current_actor`. A `DeletePreview`
  strawberry type + a `delete_with_preview(instance|queryset, *, confirm)` resolver
  there gives every rebac consumer the confirm/cascade-preview surface for free.
- **stays Angee** — only the *addressing and display* layer: `PublicID`,
  `require_instance_for_id` (sqid→row), and `public_id_of` for the node labels
  (`deletion.py:56-110` use `public_id_of`). Those are Angee's sqid boundary.

So split: the preview engine + tree + read-scope → rebac glue (generic, typed,
parameterised by a "row public id" + "row label" strategy); Angee keeps a 3-line
adapter that supplies sqid addressing/labels. Safety: medium — it is a real move
of a typed GraphQL surface; the SDL type name `DeletePreview` and field shape must
be preserved (tested in `tests/test_crud.py:108-110`,
`tests/test_delete_preview_tree.py`). Do it as a relocation with the same public
shape, then re-export from Angee for compatibility. Unlocks deleting all of
`deletion.py` except a thin sqid adapter.

Caveat / uncertainty: the node labels carry sqids (`object_id=public_id_of(...)`),
so the rebac-side type must accept an injected "public id of row" + "label of row"
callback rather than hardcoding pk — otherwise the move drags Angee's sqid concept
into rebac. Treat the row-identity strategy as the seam (mirrors the
`filter_echo_relation_identity` seam the aggregates note recommends for the same
sqid-vs-pk reason).

### 5. nestjs envelope builder — a *future* home, not a move

`strawberry-django-nestjs` exists to expose models in the nestjs-query convention
(`createOneX(input:{x:{…}})`, `updateOneX(input:{id, update})`) and today owns only
`input_to_dict` (`mutations.py:17-24`). Angee's `crud()` emits **flat** Angee-named
fields (`create_<model>`, `update_<model>`, `delete_<model>` — `crud.py:78`,
`:88-116`), not the nestjs envelope. So there is **no existing `crud.py` code to
move into nestjs**. The note in `rebac-graphql-permission-classes.md` §4 about "the
nestjs write surface" is about a *new* surface; when it is built, the
create/update/delete envelope builder belongs in `strawberry-django-nestjs`,
composing the (now-fixed) stock strawberry-django mutations. Flagged so the
prioritised list doesn't imply moving today's flat surface.

### 6. `permission_classes`/`write_context`/`_SystemContextWrite` — Angee deletion, not upstream

Per `rebac-graphql-permission-classes.md`: for standard rebac models the rebac
signals already gate create/update/delete (`signals.py`), so `permission_classes`
is redundant; where it pairs with `write_context` (admin consoles —
`agents/schema.py:641`, `:768`…, `storage/schema.py:572`, `:582`) it is load-bearing
*only because* `_SystemContextWrite` (`crud.py:30-50`) elevates the write past the
signal. The const-`admin->member` create the signal already resolves on empty-id
(`test_create_gate.py:109-121`). So the right move is **delete** `write_context`/
`_SystemContextWrite` and the paired admin `permission_classes`, letting the rebac
signal gate the const-admin write. That deletion is **in Angee** (and the addon
schemas), not an upstream relocation — listed here only to mark it as non-upstream
so it isn't mis-filed.

## Prioritised "move upstream" table (highest value + safest first)

| # | Change | Owner | Class | Safety | Unlocked deletion in Angee |
|---|---|---|---|---|---|
| 1 | `_parse_pk`/`prepare_create_update`: look up relations/PK by `{key_attr: value}` when `key_attr` ≠ pk (default-pk path unchanged) | strawberry-django fork | upstream-bug-fix | **High** — Angee owns fork; default behaviour preserved; makes `DEFAULT_PK_FIELD_NAME` actually apply to lookups | `coerce_relation_public_ids` (`ids.py:57-124`, FK/M2M helpers), `_AngeeCreateMutation` (`crud.py:135-147`), the custom branch of `_resolve_for_write` (`crud.py:233-252`), and the coercion wrap in `_AngeeUpdateMutation` |
| 2 | `DjangoMutationCUD.__copy__`: also copy `key_attr` + `argument_name` | strawberry-django fork | upstream-bug-fix | **High** — restores attrs the field already declares; behaviour-preserving | `_AngeeMutationCloneMixin` (`crud.py:125-132`) entirely |
| 3 | `RebacQuerySet.for_write()` = `on_field_deny("allow")` (+ manager passthrough) | django-zed-rebac | rebac queryset convenience | **High** — pure relocation of a rebac fact; non-rebac models simply lack it | `_write_queryset` (`crud.py:223-230`) + its duck-typed `getattr` guard |
| 4 | After 1–3: drop the mutation subclasses; `_update_mutation`/`_create_mutation` build stock `DjangoCreate/UpdateMutation` with `key_attr=PUBLIC_ID_FIELD_NAME` and `get_queryset` returning `for_write()` | Angee (consequence of 1–3) | stays Angee glue (now thin) | **High** | `_AngeeCreateMutation`, `_AngeeUpdateMutation`, `_resolve_for_write` — replaced by `get_queryset` override only |
| 5 | Move `DeletePreview` + tree + read-scope into rebac strawberry glue, parameterised by a row-identity/label strategy; Angee keeps a 3-line sqid adapter | django-zed-rebac (`rebac/graphql/strawberry_django.py`) | rebac enforcement/glue (generic preview) | **Medium** — typed-surface move; preserve `DeletePreview` SDL name/shape; re-export for compat | nearly all of `deletion.py` (~389 lines → ~10-line Angee adapter) |
| 6 | (Angee-side, not upstream) Delete `write_context`/`_SystemContextWrite` + paired admin `permission_classes`; rely on rebac signal | Angee + addon schemas | removable redundancy | **Medium** — must remove the pair together (note §5 of perm-classes) | `_SystemContextWrite` (`crud.py:30-50`), `write_context` param, `permission_classes` plumbing |
| 7 | (Future) create/update/delete **nestjs envelope** builder for the nestjs write surface | strawberry-django-nestjs | nestjs-shape generic | n/a — new code, not a move | (none today; prevents a future hand-rolled envelope in Angee) |

## Net shape `crud.py` shrinks to

After moves 1–6, `crud.py` is a thin factory with **no mutation subclasses**, no
relation coercion, no write-queryset helper, no system-context extension:

- `crud(node, *, create, update, delete, name)` — builds the flat Angee-named
  `create_<model>`/`update_<model>`/`delete_<model>` fields (the Angee naming
  convention is the only irreducible glue).
- create field = stock `strawberry_django.mutations.create(input)` (relation sqids
  resolved by the fixed library).
- update field = stock `strawberry_django.mutations.update(input, key_attr="sqid")`
  with a `get_queryset` that returns `model._default_manager.for_write()` (rebac
  convenience).
- delete field = rebac glue's `delete_with_preview` resolver addressed by
  `PublicID`/`require_instance_for_id`, returning the rebac-owned `DeletePreview`
  (re-exported from Angee).

That collapses ~270 lines of `crud.py` + ~389 lines of `deletion.py` to roughly a
40-line factory plus a ~10-line sqid delete adapter — and removes every
`getattr`/object-shape probe (`crud.py:227`) and every mutation override. The sqid
boundary (`PublicID`, `require_instance_for_id`, `instance_for_id`,
`assert_unique_sqid_prefixes`) stays in `ids.py` as the one genuinely
Angee-specific concern.

## Open questions / uncertainty to confirm before moving

- **Move 1 surface:** confirm no other strawberry-django call path relies on the
  current `get(pk=…)` for a configured non-pk `key_attr` (search the fork for
  `key_attr` lookups beyond `_parse_pk`; the m2m path at `resolvers.py:683`,`:696`
  pops `key_attr` and uses through-managers, so likely unaffected — verify).
- **Move 5 identity seam:** the rebac-side `DeletePreview` must take an injected
  "public id of row" + "label of row" strategy (Angee passes sqid/`str(instance)`),
  not hardcode pk — otherwise the move imports Angee's sqid concept into rebac.
  Mirror the `filter_echo_relation_identity` strategy pattern from
  `data-management-library-research.md`.
- **Move 6** is gated on the product decision in `rebac-graphql-permission-classes.md`
  §5 (admin reads currently 403 vs. would become silent empty list); treat reads
  separately from writes.
