# Audit ledger — src/angee/base

Scope: read-only structural audit of the `angee.base` addon against this repo's own
docs (AGENTS.md, docs/guidelines.md, docs/backend/guidelines.md, docs/stack.md).
Verified firsthand; `uv run mypy src/angee/base/` passes (28 files). Package
layering is clean: base imports neither `angee.compose` nor `angee.resources`.

- id: base-001
  loc: src/angee/base/graphql/subscriptions.py:79
  category: dead/unused code + forwarding wrapper
  rule: docs/backend/guidelines.md "A wrapper must prove it adds a real new concept. If it only forwards, normalizes, or renames a Django object, delete it." + AGENTS.md "Prefer deletion to abstraction."
  finding: `ChangeReadGate.filter` is typed `ChangeEvent | None`, so in `_gate_event` the `isinstance(filtered, ChangeEvent)` is always true and the trailing `ChangeEvent.from_payload(filtered)` fallback is unreachable; the function then only forwards `filter()`.
  fix: Delete `_gate_event` and call `ChangeReadGate(model, actor).filter(payload)` directly inside the `changes()` resolver (drop the dead isinstance/fallback).
  status: fixed

- id: base-002
  loc: src/angee/base/deletion.py:142
  category: scattered functions over a passive dataclass (missing class)
  rule: docs/backend/guidelines.md "A dataclass that only holds fields while a sibling module mutates and emits from it is a missing class." + "When several functions take the same object and read, transform, or emit from it, that object should be a class and those functions its methods."
  finding: `_PreviewRows` is a field-only dataclass whose state is read from outside by `_group_node`, and the node tree is built by loose `_root_node`/`_group_node`/`_leaf_node` that read instance/model shape and emit `DeletionPreviewNode` — node construction has no home on `DeletionPreviewNode`.
  fix: Give `DeletionPreviewNode` classmethod factories (`for_root`/`for_group`/`for_leaf`) and let `_PreviewRows` own the count/cap derivation it currently exposes for `_group_node`; keep only genuine `Collector` orchestration loose.
  status: fixed

- id: base-003
  loc: src/angee/base/models.py:110
  category: DRY — same fact in two places
  rule: AGENTS.md "Keep one source of truth per fact." + docs/backend/guidelines.md "ask `model._meta` ... rather than re-decoding model shape" / Find the owner.
  finding: The "the public id lives on the `sqid` field" fact is encoded twice via `_has_model_field(cls, "sqid")` — once in `_public_id_lookup` (line 110) and once in `_public_id_value` (line 161) — re-decoding model shape from outside instead of asking the owner (`SqidMixin`).
  fix: Centralize the sqid-presence fact once (e.g. `isinstance(self, SqidMixin)` for the AngeeModel paths) and have lookup and value read that single source; keep one shared field-probe only for the non-AngeeModel loose adapters.
  status: fixed

## Adjudicated scanner hits — not violations

- models.py:102,123 `except TypeError, ValueError:` — valid in Python >= 3.14 (parses as a tuple of types); repo pins Python >= 3.14 in docs/stack.md. Not a SyntaxError.
- models.py:115,128 `instance_from_public_id` / `public_id_of` — ownerless adapters that must also accept non-AngeeModel models (e.g. `auth.User`, which cannot host the method); docs/guidelines.md explicitly permits a loose "integration entrypoint / pure transform with no natural owner."
- serialization.py:11 `json_safe` — ownerless JSON coercion transform (Django `parse_*` precedent); legitimately module-level.
- net.py, relations.py — cohesive pure-transform / library-orchestration modules over rebac + URL/address values; permitted ownerless shape.
- apps.py:87-297, access.py:65/82, discovery.py:31, settings.py:225, schema.py:183, node.py:45, errors.py:29-31, signals.py:55, subscriptions.py:82 — isinstance against external/library types (Mapping/Sequence/QuerySet/RebacManager/rebac exceptions) or input-validation of the untyped addon-authoring contract on `AppConfig`; polymorphism is not available to us. Not type-dispatch-wanting-polymorphism.
- apps.py:154/168/255/347 deferred imports — all marked phase-1 / ready() deferrals with reason comments, exactly the carve-out in docs/backend/guidelines.md.
- apps.py:353/362 `_module_exists` / `_normalize_depends_on` — small shared pure helpers in the apps module; defensible ownerless.
