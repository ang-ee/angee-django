# Review-Fixes Round 2 — verified regressions to fix

> Adversarial verification of the round-1 fixes confirmed 10 real findings.
> Fix all of them. Same discipline as round 1: reconstruct, no provenance in
> code/commits, re-read before/after, run the gate (`ruff --no-cache`, `mypy`,
> `pytest`, `angee build --check`) before finishing, and run the example e2e on
> a **fresh/cleared ledger** (the round-1 e2e passed only because the ledger was
> pre-populated). Commit per fix group with clean messages.

## 1. CRITICAL — `resolve_xref` must accept the short addon label

`src/angee/resources/widgets.py` `resolve_xref`. Slice 3 now requires an exact
`source_addon` match against the **full** dotted name, but the ledger stores the
full name (`example.notes`) while resources cross-reference by the **short
label** (`notes.user_alice`). The example demo
(`examples/notes-angee/src/example/notes/resources/demo/020_notes.note.yaml`,
`created_by: notes.user_alice`) fails on a fresh load with
`unresolved xref 'notes.user_alice'`.

The framework treats addon `name` and `label` as interchangeable aliases
(`src/angee/base/discovery.py` `_addon_aliases` maps both → the canonical name,
raising on duplicate). The deleted `__endswith` branch honored that; the
replacement silently dropped it.

**Fix (keep the locked decision — exact match, no `__endswith`, fail-fast):**
canonicalize the addon reference through the alias registry, then exact-match.
- Build the alias map (`{name: name, label: name}` for discovered addons) once
  and bind it to the resource/widgets the same way `ledger_model` is bound in
  `AngeeResource.__init__` (so resolution costs no per-call registry scan).
  Reuse `discover_addons()` / the `_addon_aliases` logic from `angee.base`
  (resources may import base).
- In `resolve_xref`, resolve the addon part by **longest-alias-prefix** so both
  forms and dotted xrefs work deterministically:
  ```python
  parts = value.split(".")
  for cut in range(len(parts) - 1, 0, -1):
      candidate = ".".join(parts[:cut])
      if candidate in alias_map:        # label or full name
          source_addon = alias_map[candidate]
          xref = ".".join(parts[cut:])
          break
  else:
      raise ValueError(f"unresolved xref {value!r}")
  ```
  then `filter(source_addon=source_addon, xref=xref).exclude(target_id="")` with
  the existing 0/>1 fail-fast (`list(qs[:2])`).
- **Tests:** add a test that a **label-form** xref resolves; update
  `test_resolve_xref_requires_exact_source_addon` so it reflects that a label
  alias resolves (it currently codifies the broken behavior); add an
  **end-to-end** test that loads the example demo (or an equivalent label-form
  fixture) on a freshly migrated/cleared ledger and asserts the FK resolves.

## 2. HIGH — WebSocket actor context is dead code

`src/angee/base/consumers.py`. The `execute_operation` override is an HTTP-path
method (`strawberry.http.async_base_view.AsyncBaseHTTPView`) and is never called
on the WebSocket transport, so WS operations run with no ambient REBAC actor.

**Fix:** find the correct strawberry-channels hook to bracket WS operation
execution in `actor_context(scope_actor(self.scope))` — investigate
`strawberry.channels` `GraphQLWSConsumer` / the GraphQL-over-WS handler and the
schema `execute` path. If no clean per-operation hook exists, **remove the dead
override** and document the limitation honestly (do not ship dead code that
claims to install the actor); the subscription read-gate already carries the
actor explicitly via `_actor_from_info`.
- **Tests (findings 6 & 7):** the WS tests currently call `execute_operation`
  directly and over-mock (`_subscribe`, `_gate_event`, `sync_to_async`), so they
  prove nothing about real dispatch. Rewrite them to exercise the real WS
  dispatch path, or assert only what is genuinely covered. Do not leave a test
  whose name claims actor-context coverage it does not provide.

## 3. MEDIUM — fail-fast on cross-model `(source_addon, xref)` reuse

`src/angee/resources/loader.py` (`_upsert_ledger` / `_instance_from_ledger`).
With identity now `(source_addon, xref)`, loading the same `(addon, xref)` for a
different model in a later run silently overwrites the ledger row and orphans the
prior target (`_check_xref_collisions` only catches within one run).

**Fix:** when an existing `(source_addon, xref)` ledger row's `target_model`
differs from the model being loaded, raise `ResourceLoadError` (collision)
instead of silently re-pointing it. Add a test.

## 4. MEDIUM — fast-delete preview test is vacuous

`tests/test_deletion.py` `test_deletion_preview_counts_fast_deletes`. Global
`pre_delete`/`post_delete` listeners in the test env make
`Collector.can_fast_delete` return False, so the new `for queryset in
collector.fast_deletes:` loop is never exercised.

**Fix:** make the test actually hit the fast-delete path (e.g. temporarily
disconnect the global signal listeners for the test model, or unit-test the
counting against a constructed `Collector` whose `fast_deletes` is populated), or
remove the misleading test rather than imply coverage that does not exist.

## 5. MEDIUM — documented bootstrap `schema --check` always fails on a fresh build

`AGENTS.md` / `CLAUDE.md` Run-From-Root (~line 144) and the Slice 8/10 step in
the round-1 plan. `angee build` no longer writes `runtime/schemas/*.graphql`
(SDL moved to `manage.py schema`), and `_is_checked_source` excludes `schemas/`,
so `manage.py schema --check` on a fresh build finds no SDL and fails.

**Fix:** correct the documented sequence to write SDL before checking — either
add `manage.py schema` before `manage.py schema --check`, or use `manage.py
schema` (write) in the bootstrap. Keep the sequence internally consistent.

## 6. LOW — strengthen the SET_NULL update test

`tests/test_deletion.py` `test_deletion_preview_counts_set_null_updates`. Use
**more than one** updated child so `sum(len(group) for group in object_groups)`
is distinguishable from the discarded `.values()`-on-a-list shape.

## 7. LOW — remove the dead `allow_non_dev` parameter

`src/angee/resources/managers.py` `_import_groups` declares `allow_non_dev` then
`del`s it; `load_addons` threads it in for nothing (the DEMO/DEBUG guard stays in
`load_addons`). Remove the parameter and the pass-through.

## 8. LOW — fix the overstated layering doc invariant

`docs/backend/guidelines.md` (~line 75). The enforced invariant is import-only
("must not **import** `angee.compose` or `angee.resources`"). Slice 9 escalated a
bullet to "must not **name** or import," which `src/angee/base/settings.py`
legitimately violates (it names both as `INSTALLED_APPS` string constants,
`RESOURCES_APP`/`COMPOSE_APP`). Reword to "must not import" (naming a string app
path is fine and necessary).

---

**Do not** edit `docs/stack.md` or any frontend file. Report the PyYAML
owner-row gap again if still open. After fixing, re-run the full gate and the
fresh-ledger example e2e, and confirm finding #1's end-to-end load now succeeds.
