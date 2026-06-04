# Resources addon — structural audit findings

Judged against AGENTS.md, docs/guidelines.md, docs/backend/guidelines.md,
docs/stack.md. Read-only; no source modified.

- id: resources-001
  loc: src/angee/resources/managers.py:14
  category: function-local-or-cross-seam import
  rule: docs/backend/guidelines.md "Imports go at the top of the module. A function-local or deferred import is a smell that a module boundary is wrong — a layer reaching across a seam — so fix the seam (move the shared fact to its owning module) instead of hiding the import"; AGENTS.md "Find the owner / never re-derive from outside what the owner knows"
  severity: high
  finding: ResourceQuerySet reaches across the seam into a leading-underscore private of another package (`from angee.base.discovery import _addon_aliases`) to compute the addon alias map the loader needs.
  fix: Expose the alias map as a public owner-method (e.g. on the addon set / discovery) and import the public name; the underscore name is a private of `angee.base.discovery` and must not be a cross-package contract.

- id: resources-002
  loc: src/angee/base/apps.py:35
  category: DRY / wrong-level duplicated fact
  rule: AGENTS.md DRY "A fact lives once, at the level that owns it … When the same idea appears twice, find the owner and remove the copy"; docs/backend/guidelines.md "Persisted choices live beside the model field, usually as model-owned TextChoices"
  severity: high
  finding: The resource tier vocabulary is declared three times — `RESOURCE_TIER_VALUES=("master","install","demo")` in base/apps.py:35, the owning `ResourceTier` TextChoices in resources/tiers.py:12-14, and `FROZEN_TIERS={"install","demo"}` in resources/entries.py:80 — and base/apps.py:313 re-validates a tier value that `ResourceTier.from_value` already owns.
  fix: Let `ResourceTier` (the model-owned choices) own the tier set and its validation; `entries.FROZEN_TIERS` (same package) should reference the enum, not re-list literals. base cannot import resources, so keep base's copy only if the layering forbids the import — but record it as the deliberate single duplication and delete the in-package copies.

- id: resources-003
  loc: src/angee/resources/entries.py:128
  category: forwarding/normalizing wrapper duplicating an owner
  rule: docs/backend/guidelines.md "A wrapper must prove it adds a real new concept. If it only forwards, normalizes, or renames a Django object, delete it"; AGENTS.md "never re-decode from the outside what the owner already knows"
  severity: medium
  finding: `ResourceEntry.from_declaration` re-normalizes `depends_on` (str→tuple) and `adopt` (str|bool) and path/url shorthand, but `BaseAddonConfig._resource_entries`/`_resource_entry` (base/apps.py:300-311) already normalized the manifest before `resource_manifest` hands it over — the entry decodes shorthand the owner already resolved.
  fix: Pick one normalizer. Either the AppConfig emits fully-typed declarations and `from_declaration` just maps fields, or `from_declaration` owns it and the AppConfig stops; do not normalize the same shape at two levels.

- id: resources-004
  loc: src/angee/resources/ordering.py:15
  category: scattered functions over a passive collection (missing collection owner)
  rule: docs/backend/guidelines.md "When several functions take the same object and read, transform, or emit from it, that object should be a class and those functions its methods … Keep a module-level function only for orchestration that genuinely has no owner"; AGENTS.md "collection behavior lives on the collection abstraction"
  severity: medium
  finding: `order_entries` plus `_entry_key`/`_dependency_key` form a topological-sort over a sequence of ResourceEntry, decoding entry shape (addon.name, source, depends_on) from outside; this is collection behavior with no home — ResourceQuerySet calls it as a loose helper.
  fix: Move the dependency ordering onto the entry collection (e.g. an `EntryGraph`/ entry-set class, or a queryset/manager-owned method) so the graph keys and sort live beside the entries they read, not in a free function.

- id: resources-005
  loc: src/angee/resources/entries.py:440
  category: DRY duplicated normalizer
  rule: AGENTS.md DRY "Same rule in two places: choose the owner, delete the copy"
  severity: low
  finding: `_normalize_label` (entries.py:440, lowercases/strips a model label for conflict comparison) overlaps the model-label canonicalization Django already owns via `make_model_tuple` (used in `resolve_model`, entries.py:48); two different label-normalization rules coexist in one module.
  fix: Compare model identity through the resolved model (`_meta.label_lower`) or `make_model_tuple`, which already owns label canonicalization, and delete the ad-hoc string normalizer.

- id: resources-006
  loc: src/angee/resources/entries.py:402
  category: DRY parallel result tallies
  rule: AGENTS.md DRY "Same shape in three places: extract the smallest boring primitive"; docs/guidelines.md "code is bigger instead of smarter"
  severity: low
  finding: The created/updated/skipped tally is expressed twice — `LoadResult` (entries.py:412) holds the three counts while `result_counts` (loader.py:507) recomputes them by switching on `RowResult.import_type`, and `_import_groups` (managers.py:88-120) re-sums them into a third set of locals.
  fix: Give `LoadResult` a classmethod factory (e.g. `LoadResult.from_rows`) that tallies `RowResult.import_type`, and have it support accumulation, so the import_type→count mapping lives once on its owner.
