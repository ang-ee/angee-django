---
name: angee-resources
description: "Declares and loads an addon's master, install and demo data: pyproject.toml [tool.angee.resources] tiers, tiered resource YAML rows, adopt keys, cross-addon depends_on, grants files, catalogue tiers, resource_class adapters, retired_models. Use for seed, fixture, catalogue, demo data."
---

# Angee Resources

## Use when

- "Seed `<Model>` rows / add demo data / add a currency or vendor catalogue to `<addon>`."
- "Make the `<addon>` seed reference rows from `iam`, `integrate` or another addon."
- "Grant a role or relation from a resource file" (`kind = "grants"`).
- "Persist a seeded row through its owner verb" (`resource_class`, `after_resource_load`).
- "Remove a model whose table only holds seeded rows" (`retired_models`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/pyproject.toml` `[tool.angee.resources]` | which files load, their tier, `adopt`, `depends_on`, `kind`, `model`; `retired_models` | `[tool.angee.resources]` in `src/angee/money/pyproject.toml` and `examples/src/example/notes/pyproject.toml` |
| `<addon>/resources/<tier>/NNN_<label>.<model>.yaml` | the rows; the filename names the target model | `src/angee/tags/resources/demo/010_tags.tag.yaml` |
| `src/angee/resources/entries.py`, `tiers.py` | manifest parsing, filename inference, dependency order, grant rows, the row xref rule; the three tiers and their prerequisite order | `resource_manifest_for`, `retired_models_for`, `ResourceEntry`, `ResourceEntry.normalize_xref`, `infer_model_label`, `EntryGraph.ordered`, `GrantRow`; `ResourceTier`, `with_prerequisites` |
| `src/angee/resources/loader.py` | resolving rows through the ledger, header validation, adoption, catalogue-tier check, seeded transition state, ledger hash | `AngeeResource`, `after_init_instance`, `after_save_instance`, `_row_content_hash`, `_adoption_fields`, `_unique_adoption_field`, `_validate_headers`, `_validate_catalogue_tier`, `build_resource` |
| `src/angee/resources/mixins.py` | `resource_class`, `catalogue`/`catalogue_tier(s)`, cooperative `after_resource_load`, check `angee.E014` | `ResourceLoadMixin`, `is_catalogue_model`, `check`, `after_resource_load` |
| `src/angee/resources/models.py`, `managers.py` | the ledger `(source_addon, xref)` and its target-model compatibility; ledger writes for imports and owner-bound identities; validate/load/diff/`load_xref`/`resolve`; demo guard, exclusions, xref collisions, merge guard | `Resource`, `Resource.require_target_model`; `retired_seed_keys`, `seeded_record_merge_guard`, `ResourceManager`, `upsert_target`, `bind_instance`, `load_addons`, `load_xref`, `_check_load_tiers`, `_excluded_entry_keys`, `_check_xref_collisions` |
| `src/angee/resources/grants.py`, `management/commands/resources.py` | `kind = "grants"` rows into REBAC tuples, idempotent by tuple key; the `resources validate\|load\|diff` command and tier selection | `materialize_grant_groups` and the module docstring; `Command` |
| `src/angee/resources/build.py`, `django_compose.migrations` | catalogue projection onto runtime models; the populated-table drop guard and its retirement exemption | `CatalogueIntegration`, `retired_primary_keys`; `DropGuardAutodetector` |

## Reuse first

- The loader is django-import-export + tablib composed by `angee.resources` ([stack](../../../docs/stack.md#backend),
  `django-import-export` row): never a `loaddata` fixture, a row-creating command or a `post_migrate` seeder.
- Identity is the ledger's `(source_addon, xref)`; relations name rows `<addon label or name>.<xref>`
  (`widgets.py` `split_xref`, `resolve_xref`).
- Runtime code reaches a seeded row with `Resource.objects.resolve(handle, model=…)` (`managers.py`)
  or re-imports one with `load_xref` (`managers.py`); tests take the concrete ledger from
  `angee.resources.testing` (the `src/angee/resources/testing/__init__.py` module docstring).
- Shared catalogues exist: `integrate.Vendor` (`src/angee/integrate/models.py`), `money.Currency`
  (`src/angee/money/models.py`); contribute rows with `adopt`.
- Owner-verb adapters exist: `WorkflowDefinitionResource`, `TriggerResource` (`src/angee/workflows/resources.py`);
  non-row declarations use `ResourceEntryKind` (`src/angee/resources/kinds.py`).

## Steps

1. **Pick the tier and bind catalogue models to it.** `master` = reference data every
   deployment carries; `install` = rows the deployment needs to function (rules, grants);
   `demo` = sample rows, loaded only under `DEBUG` or `--allow-non-dev` (`ResourceManager._check_load_tiers` in `managers.py`).
   A model several addons seed declares its tier once (`Currency` in `money/models.py`):

   ```python
   class Currency(ResourceLoadMixin, ArchiveMixin, AngeeDataModel, concrete=True):
       catalogue = True
       catalogue_tier = "master"          # or catalogue_tiers = ("master", "install")
       code = models.CharField(max_length=3, unique=True)

       class Meta:
           abstract = True                # a composition source must be abstract (ModelComposition.discover)
           rebac_resource_type = "money/currency"
   ```
   **Owner rule:** the model owns its tier, read without inheritance (`ResourceLoadMixin.get_catalogue_tier` in `mixins.py`); a
   manifest that disagrees fails at load (`_validate_catalogue_tier` in `loader.py`)
   ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).

2. **Write `resources/<tier>/NNN_<label>.<model>.yaml`.** `NNN` orders siblings; the loader
   infers the model from `<label>.<model>` (`ResourceEntry.infer_model_label` in `entries.py`) or from `_meta.model`. Use the
   nested form below or the flat form (`- xref: …` plus field keys). Relations name rows by
   xref; many-to-many takes a list; a content-type relation takes a model label.

   ```yaml
   _meta:
     model: money.CurrencyRate               # money/resources/demo/020_money.currencyrate.yaml
   rows:
     - xref: usd_20260101
       fields:
         currency: money.usd                 # <addon>.<xref> of the master catalogue row
         date: 2026-01-01
         rate: "1.0000000000"
   ```
   If the model has a field named `model` (`agents.Agent`) → nested form only
   (`_values_for` in `entries.py`). If a field is guarded by `StateTransitions` → seed the initial
   value only ([seeded transition state](../../../docs/backend/guidelines.md#seeded-transition-state)).
   **Owner rule:** row keys are model fields cleaned by the native import; primary keys and
   unknown headers are refused (`_validate_headers` in `loader.py`), entry and file labels must agree
   (`ResourceEntry._check_model_conflict` in `entries.py`) ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).

3. **Declare the entry in `pyproject.toml`.** A bare path suffices for an independent file.

   ```toml
   [tool.angee.resources]                                          # money/pyproject.toml
   master = [{ path = "master/010_money.currency.yaml", adopt = "code" }]
   demo = [
     { path = "demo/020_money.currencyrate.yaml", depends_on = "master/010_money.currency.yaml" },
   ]
   ```
   If a row's natural key may already exist (shared catalogue, operator-created row) →
   `adopt = "<unique field>"` or `adopt = ["f1", "f2"]`, the exact field set of a model-owned
   `unique`, `unique_together` or non-expression `UniqueConstraint` (`_unique_adoption_field` in `loader.py`;
   `notes/pyproject.toml` adopts `["user", "name"]` through `Credential`'s conditional
   constraint). If a relation names another addon's seed → `depends_on = "<label>:resources/…"`
   in a tier at or below yours (`EntryGraph.ordered` in `entries.py`). **Owner rule:** the manifest selects files
   and keys; the model owns uniqueness, so no field is adoptable without it
   ([addon declarations](../../../docs/composer.md#addon-declarations)).

4. **Grant relations with `kind = "grants"`.** Declare the entry as
   `{ path = "demo/030_iam.admin_grant.yaml", kind = "grants" }` (`iam/pyproject.toml`),
   ordered after the rows it names. A row xref resolves through the ledger, a literal is
   `<ns>/type:<id>[#relation]`, `*` is the anonymous subject.

   ```yaml
   rows:
     - resource: angee/role:admin           # iam/resources/demo/030_iam.admin_grant.yaml
       relation: member
       subject: angee.iam.user_admin
   ```
   **Owner rule:** the loaded row owns its REBAC type and the native writer validates the
   tuple (the `grants.py` module docstring); a grants file declares no model (`ResourceEntry.read_grant_rows` in `entries.py`).

5. **Persist through an owner verb only when the model requires it.** Set `resource_class`
   on a `ResourceLoadMixin` model to an `AngeeResource` subclass (`build_resource` in `loader.py` refuses
   anything else). The adapter keeps the native hooks and hands the saved identity back to
   the row instance, because `after_save_instance` ledgers that instance (`loader.py`):

   ```python
   class Workflow(ResourceLoadMixin, OwnerMixin, HistoryMixin, AngeeDataModel):  # workflows/models.py
       resource_class = WorkflowDefinitionResource

   class WorkflowDefinitionResource(AngeeResource):                             # workflows/resources.py
       def save_instance(self, instance, is_create, row, **kwargs):
           self.before_save_instance(instance, row, **kwargs)
           installed = type(instance).objects.install_definition(key=instance.key, ..., actor=kwargs.get("actor"))
           for field in instance._meta.concrete_fields:                        # saved fields -> row instance
               setattr(instance, field.attname, getattr(installed, field.attname))
           instance._state.adding = False                                      # saved state -> row instance
           instance._state.db = installed._state.db
           self.after_save_instance(instance, row, **kwargs)
   ```
   For batch work, override `after_resource_load` and call `super()` once (`Dashboard.after_resource_load` in `src/angee/dashboards/models.py`).
   **Owner rule:** the adapter delegates to the manager and keeps the native ledger hooks, adding
   no importer or ledger of its own ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).

6. **Retire a seeded model.** If the table holds only rows this addon's files seeded →
   `retired_models = ["messaging.ActivityType"]` under `[tool.angee.resources]` (`src/angee/messaging/pyproject.toml`);
   otherwise → a data migration ([migration history](../../../docs/backend/guidelines.md#migration-history)).
   **Owner rule:** the drop guard exempts only this addon's ledgered keys (`CatalogueIntegration.retired_primary_keys` in `build.py`, `retired_seed_keys` in `managers.py`);
   removing a manifest entry retires nothing ([addon declarations](../../../docs/composer.md#addon-declarations)).

7. **Load and reload.** `resources load` applies `master` and `install`; `--include-demo`
   adds `demo`; a selected tier brings its prerequisites (`ResourceTier.with_prerequisites` in `tiers.py`). A deployment that
   must skip one entry sets `ANGEE_RESOURCE_EXCLUDED_ENTRIES` (`_excluded_entry_keys` in `managers.py`).
   **Owner rule:** idempotency is the ledger's `(source_addon, xref)` plus the row content
   hash (`Resource.Meta` in `models.py`, `_row_content_hash` in `loader.py`); an unchanged replay touches no row
   ([resource loading](../../../docs/backend/guidelines.md#resource-loading)).

## Do not

- A row that carries `id`, a pk or a public sqid: the ledger's xref owns identity, so the header check refuses them (`_validate_headers` in `loader.py`).
- A `loaddata` fixture, seeding command or `post_migrate` hook for rows the loader can import: the native import lifecycle already owns them ([stack](../../../docs/stack.md#backend), `django-import-export` row).
- A resource file without a `[tool.angee.resources]` entry: the manifest selects files, so an unlisted file loads nothing ([resource pitfalls](references/pitfalls.md)).
- Promoting a demo row to `install`, or editing another addon's seed, to remove a permissive row: tiers are additive per addon, so the deployment excludes the entry instead ([demo tiers are additive](../../../docs/backend/guidelines.md#resource-loading)).
- Selecting a seeded row by display name when it has an xref or stable key: the ledger owns the lookup ([seeded rows carry a stable key](references/pitfalls.md)).
- Seeding fields that `StateTransitions` writes (`workspace`, `runtime_status`, `last_error`): the transition owns them and the loader does not protect them ([seeded transition state](../../../docs/backend/guidelines.md#seeded-transition-state)).
- A catalogue tier on `AppConfig` or a second registry, or `runtime/` model imports to resolve a seed: the source model owns the tier and the ledger owns resolution ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).
- Caveated REBAC subjects behind a grant: actor-scoped querysets have no caveat context, so `angee.E024` rejects them (`check_rebac_caveats` in `src/angee/base/checks.py`) and grants are caveat-free (the `grants.py` module docstring).

## Verify

Done means: `check` reports no `angee.E014`; `resources validate --include-demo` counts the
new file and rows; `resources load` creates the rows and a second run reports 0 created,
0 updated (the idempotency proof; a grants file reports only `skipped`). Commands and root:
[composition and schema](../../../docs/checks.md#composition-and-schema).

- In a lane (jj workspace: no host, database or JS install): focused pytest against the
  `angee.resources.testing` ledger ([source-addon test models](../../../docs/checks.md#source-addon-test-models)),
  `tests/test_catalogue_trait.py`, ruff and mypy on touched files ([Python contracts](../../../docs/checks.md#python-and-composition-contracts)).
- Only on integrated `src`: `resources validate`/`load`/`diff`, `check`, and the browser
  suite that reads the demo rows ([browser verification](../../../docs/checks.md#browser-verification)).
- Loop: `.agents/skills/slice/SKILL.md`. False greens: `validate` is a rolled-back dry run that
  skips `after_resource_load` (`ResourceManager._import_groups` in `managers.py`), omits `demo` without `--include-demo`, and counts zero rows for an unlisted file.



## Pitfalls

- A master entry cannot `depends_on` a demo entry: the target is outside the selected tiers and the load fails (`EntryGraph.ordered` in `entries.py`, `ResourceTier.with_prerequisites` in `tiers.py`).
- One xref per addon across every file and tier; a repeat fails the whole load (`_check_xref_collisions` in `managers.py`).
- `adopt` on a field without model-owned uniqueness fails at load, not at `check`; a composite key must equal a constraint's exact field set (`_unique_adoption_field`, `_has_unique_field_set` in `loader.py`).
- A changed seeded `status` is validated, then discarded on update; only a new row takes it ([seeded transition state](../../../docs/backend/guidelines.md#seeded-transition-state), `after_init_instance` in `loader.py`).
- Merging a seeded record away is refused because the next load would recreate it (`seeded_record_merge_guard` in `managers.py`).
- A flat-form row whose model has a `model` field is rejected as reserved; use the nested form (`_values_for` in `entries.py`).

## Read next

- [Resource pitfalls](references/pitfalls.md)
- [Resource loading](../../../docs/backend/guidelines.md#resource-loading), [Composition owners](../../../docs/backend/guidelines.md#composition-owners), [Migration history](../../../docs/backend/guidelines.md#migration-history)
- [Addon declarations](../../../docs/composer.md#addon-declarations), [Project migration history](../../../docs/composer.md#project-migration-history)
- [Composition and schema](../../../docs/checks.md#composition-and-schema), [Glossary: Resource file](../../../docs/glossary.md#backend); skills `angee-addon`, `angee-models`, `angee-permissions`, `angee-tests`, `slice`
