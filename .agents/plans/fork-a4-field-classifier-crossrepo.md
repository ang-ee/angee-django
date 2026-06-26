# A4 — Field-classifier owner resolution (cross-repo execution plan)

> Status: PLAN ONLY. Resolves plans-audit item **A4** (`harmonisation-cleanup-plan.md`), currently "in limbo," by finishing it at the owner level — or, if the upstream change is out of scope, formally closing it as architect-deferred (§7).
>
> Repo root: `/Users/alexis/Work/angee/angee-django`. Sibling owner: `/Users/alexis/Work/angee/strawberry-django-aggregates` (git `ang-ee/strawberry-django-aggregates`, version `0.8.0`, consumed as a **pinned PyPI release**, not editable).

## 0. The load-bearing constraint (read first)

The consolidated audit's **"Do NOT touch"** rejection is binding: do NOT fuse the four field-type `isinstance` ladders into one classifier — a unified classifier would lose Decimal/Float/Time distinctions and re-implement stack-owned behaviour. So A4 is **not** "collapse four ladders." It is: **for each derived fact, identify whether a library owns it; route the one fact that has an owner to that owner; leave the genuinely ownerless Angee dialect glue alone.**

**`[architect-decision]`** The one escalated judgement: is publishing one new public symbol from the aggregates library (`group_by_alias`) worth a minor bump + manifest churn, given the alias has been byte-stable since inception? §7 is the documented fallback if "not now."

## 1. Fact-by-fact owner classification (verified against installed source)

The four "classifiers" + the recomputed alias derive **five distinct facts**:

| # | Symbol | Fact | Owner verdict |
|---|---|---|---|
| 1 | `hasura.py:680 _scalar_for_field` | Django field → Hasura **wire-scalar token** (`Decimal→"Float"`, `DateTime→"DateTime"`) | **Angee dialect glue, NO owner.** `field_type_map` maps to Python *types* (keeps Decimal distinct); Angee collapses `Decimal/Float→"Float"` as a Hasura decision. **Keep.** |
| 2 | `metadata.py:844 _surface_field_scalar_or_none` | Strawberry surface type → same wire-scalar token (more authoritative; reads the built surface) | **Angee dialect glue, NO owner.** Same vocab as #1. **Keep** (internal #1↔#2 fusion is out of A4 scope — latent, undemonstrated). |
| 3 | `hasura.py:672 _measure_ops_for_field` | Django field → valid aggregate ops | **Library-owned, ALREADY PUBLISHED:** `strawberry_django_aggregates.default_operators_for(field_type)` (public `__all__`). Angee's list is a curated subset. **Route via intersection (§3b).** |
| 4 | `metadata.py:991 _field_widget` | Django field → UI widget | **Angee/console dialect glue, NO owner.** Frontend-rendering decision. **Keep verbatim.** |
| 5 | `hasura.py:661 _group_key_path` + `f"{path}_id"` | FK group axis → canonical group-key wire alias | **Library-owned, NOT YET PUBLISHED:** `strawberry_django_aggregates.compiler.group_by_alias` (byte-identical logic) — used internally everywhere but absent from `__all__`. **This is the cross-repo change (§2).** |

### 1a. Explicit non-targets (do not touch under A4)
- **`out[f"{key}_id"]` at `hasura.py:138`** — a Django ORM **write kwarg** in the public-id decoder, not a group-key alias; shares `_id` by Django convention only. Out of scope.
- **`is_to_one_relation`** — the findings note "defined in both files" is **stale**; already a single owner at `angee/graphql/introspection.py:37`, imported by both. No work.
- **`revisions.py:_field_annotation`** — a real fix but duplicates a *different* map (`field_type_map`), tracked separately, explicitly carved out of A4. (Note: already landed in the DRY pass — commit `bd773924`.)
- **`_scalar_for_field` ↔ `_surface_field_scalar_or_none` fusion (#1↔#2)** — same-vocab scalar paths, no external owner, divergence latent/undemonstrated → out of A4 (file separately if ever demonstrated).

**Net A4 surface: exactly two facts have an external owner — #3 (op list, owner published) and #5 (group alias, owner must publish). Everything else stays.**

## 2. Cross-repo change — publish `group_by_alias` from `strawberry-django-aggregates`

`[cross-repo-access]` The function already exists and is battle-tested internally — this is a **visibility promotion** (same change class as the 0.7.0 `shape_aggregate_row`/`make_group_order_input` promotions; that is the template).

### 2a. Upstream edits (sibling repo)
1. `__init__.py` — import `group_by_alias` from `.compiler` and add `"group_by_alias"` to `__all__` (under a "Vocabularies/Aliasing" grouping next to `default_operators_for`); extend the module-docstring public-surface list.
2. `compiler.py:1545` — no logic change; ensure the docstring is consumer-grade; optionally note "Public; consumers MUST NOT recompute this."
3. `docs/SPEC.md` — add `group_by_alias` to the §16 "1.0 SemVer surface" list (it is already referenced canonically at :201/:365/:845).
4. Tests — a focused public-contract test (`from … import group_by_alias` + the four canonical outputs: FK→`_id`, plain passthrough, TIME bucket, NUMBER bucket) to freeze the contract.
5. `CHANGELOG.md` — `### Added` entry; no behaviour/SDL change.
6. Version bump `0.8.0 → 0.9.0` (`pyproject.toml` + `__version__`) — MINOR per the lib's 0.x SemVer policy.

### 2b. Version bump → manifest sequence `[architect-decision]`
The aggregates lib is a **pinned PyPI release** (`uv.lock` registry `0.8.0`, `pyproject.toml:25` `>=0.8.0`) — contrast `strawberry-django-hasura`, which is editable (`../strawberry-django-hasura`). So the new symbol is invisible to Angee until re-resolved.
- **Path A (dev, recommended) — temporary editable sibling**, mirroring hasura/rebac: add `strawberry-django-aggregates = { path = "../strawberry-django-aggregates", editable = true }` to `[tool.uv.sources]`, `uv lock && uv sync`, with the same "switch to a version pin once published" comment. Unblocks Angee work immediately. (`strawberry-django-hasura` already depends on aggregates `>=0.7.0`, so an editable aggregates resolves transitively cleanly.)
- **Path B (final) — publish `0.9.0`, then pin** `>=0.9.0` and drop the editable source entry.
- **Sequence:** dev on Path A → land §3 against the editable sibling → architect approves both diffs → publish 0.9.0 → flip to Path B → final `schema --check` on the pinned release. Do not leave the editable entry in `main`.

## 3. Angee-side collapse (consume the two contracts; delete the recomputes)

### 3a. Fact #5 — group alias → `group_by_alias`
- Import `group_by_alias` from `strawberry_django_aggregates` in `hasura.py`.
- `_group_key_path` (`:661-669`): delegate the FK-alias decision to `group_by_alias(path, None, field)`; keep the local `path.replace(".", "__")` non-relation normalization (Angee path-syntax glue, no owner). `[architect-decision]` verify Angee's predicate (`"__" not in path and is_to_one_relation(field)`) ≡ the library's (`field.is_relation and field.many_to_one`) for Angee's axes — the curation gate (§4) is the oracle. Deletes the `f"{path}_id"` literal; the FK-alias *rule* gets one owner. The inline shapes at `:498`/`:511` read `key = _group_key_path(...)` so inherit the fix.

### 3b. Fact #3 — op list → `default_operators_for` ∩ curation
- Replace `_measure_ops_for_field` (`:672-677`): resolve `default_operators_for(type(field).__name__)` (→ `.value`), then **intersect with an explicit local `_ANGEE_CURATED_OPS = ("sum","avg","min","max")`**, preserving the curated order (metadata JSON is order-sensitive for `schema --check`). Net: the *vocabulary* (which Django types are numeric/date) is owned upstream and can't drift; the *curation* (advertised subset) stays an explicit Angee decision. Keep the `primary_key` skip (`:634`). `[architect-decision]` confirm the curation set stays Angee-side (the lib intentionally has no opinion on the advertised subset).

### 3c. What stays (the deletion signal)
- `_scalar_for_field` (#1), `_surface_field_scalar(_or_none)` (#2), `_field_widget` (#4): **unchanged.** Only deletions: the `f"{path}_id"` recompute and the hand-rolled op-classification body. Two imports added, **zero `isinstance` ladders removed.** If a diff deletes `_scalar_for_field`/`_field_widget`, **A4 has overreached into the forbidden fusion — reject.**

## 4. Risk — emitted artifacts; the SDL + metadata oracle

Both touched facts flow into emitted, frontend-consumed artifacts. **Expected delta: zero** (logic byte-identical / curation clips), but must be *proven* zero.
- **Oracle:** `uv run examples/notes-angee/manage.py schema --check` — `GraphQLSdl.check()` (`sdl.py:79`) drifts on **both** SDL files *and* `…metadata.json` (`group_dimensions[].key`, `aggregate_measures[]`). A clean check **with committed artifacts unregenerated** proves byte-identical emission. **Primary pass/fail.**
- Any drift → **stop-and-escalate** (a deliberate alias change needs frontend codegen + document updates and is out of A4 scope).
- Frontend codegen oracle only if step 1 shows an intentional delta (expected: confirmatory no-op).
- **Curation-intersection pin test:** assert `_measure_ops_for_field` output equals the pre-change tuple for a model with Int/Decimal/Float/Date/DateTime fields — so an upstream op-list change can never silently widen Angee's advertised ops.
- Backend tests for `angee/graphql/data/` + `uv run mypy` (new imports type-check). Run the aggregates lib's own pytest before publishing.
- Note: A4 does **not** touch the scalar path, so the Decimal→Float token is unchanged — if a scalar diff appears, something is wrong.

## 5. Orchestration (cross-repo)

```
Phase S — Sibling repo (../strawberry-django-aggregates)   [cross-repo-access]
  S1 branch · S2 promote group_by_alias to __all__ · S3 SPEC+CHANGELOG+public test ·
  S4 lib pytest green · [architect sign-off #1: approve permanent public-API addition] · S6 bump 0.9.0
Phase M — Manifest (angee-django)                          [cross-repo-access]
  M1 editable [tool.uv.sources] entry (Path A) + uv lock + uv sync
Phase A — Angee collapse
  A1 consume group_by_alias (3a) · A2 curation-intersection (3b) · A3 pin test + data tests + mypy ·
  A4 `schema --check` MUST be clean / zero drift (any drift → STOP, escalate)
Phase R — Release + repin
  [architect sign-off #2: equivalence holds + curation stays Angee-side] · publish 0.9.0 ·
  flip to Path B (pin >=0.9.0, drop editable entry, uv lock) · final schema --check + mypy ·
  tick A4 + the two Backend-Rider boxes
```
**Two required architect sign-offs:** #1 before the sibling release (permanent public contract; the core decision; also confirms Path A/B + the minor bump); #2 before publish+repin (emitted-artifact equivalence + curation placement). Net shape: "two recomputes deleted, two owner imports added, four ladders untouched."

## 6. Sequencing dependencies
- Fact #3 (`default_operators_for`) has **no upstream blocker** — could land in Phase A alone.
- Fact #5 (`group_by_alias`) **blocks on Phase S.** Phase A blocks on Phase M. Phase R blocks on sign-off #2 + a real PyPI release. Do not skip the final repin.

## 7. Fallback — formal architect-deferred close `[architect-decision]`

If sign-off #1 declines the upstream change, A4 closes as **architect-deferred** (ending the limbo), not worked around. Document: (1) decision + owner + date; (2) why the recompute is acceptable interim (`f"{path}_id"` is byte-identical to `compiler.group_by_alias`, both test-covered, drift latent); (3) **a trip-wire:** any change to `compiler.group_by_alias` is a breaking change for Angee until A4 is done — record in the sibling's CHANGELOG/SPEC so a future alias change forces re-open; (4) **still land §3b regardless** (fact #3 has no upstream dependency — secure op-vocabulary ownership even in the fallback); (5) reconcile the plan docs (flip A4 from "in limbo" to "Architect-deferred (alias #5); op-list #3 landed").

### Critical files
- `../strawberry-django-aggregates/strawberry_django_aggregates/__init__.py` (publish `group_by_alias`; `__version__`)
- `../strawberry-django-aggregates/strawberry_django_aggregates/compiler.py` (`group_by_alias` :1545; `operators.py:146 default_operators_for`)
- `angee/graphql/data/hasura.py` (`_group_key_path` :661, `_measure_ops_for_field` :672; delete the `f"{path}_id"` recompute)
- `pyproject.toml` (`:25` pin + `[tool.uv.sources]` editable→pin) + `uv.lock`
- `angee/graphql/sdl.py` (the `schema --check` oracle covering SDL + `.metadata.json`)
