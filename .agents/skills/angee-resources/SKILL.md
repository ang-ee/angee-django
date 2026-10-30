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
| `<addon>/pyproject.toml` `[tool.angee.resources]` | which files load, their tier, `adopt`, `depends_on`, `kind`, `model`; `retired_models` | `src/angee/money/pyproject.toml:8-12`, `examples/src/example/notes/pyproject.toml:27-42` |
| `<addon>/resources/<tier>/NNN_<label>.<model>.yaml` | the rows; the filename names the target model | `src/angee/tags/resources/demo/010_tags.tag.yaml` |
| `src/angee/resources/entries.py`, `tiers.py` | manifest parsing, filename inference, dependency order, grant rows; the three tiers and their prerequisite order | `resource_manifest_for:97`, `retired_models_for:111`, `ResourceEntry:221`, `infer_model_label:406`, `EntryGraph.ordered:558`, `GrantRow:632`; `ResourceTier:11`, `with_prerequisites:32` |
| `src/angee/resources/loader.py` | xref identity, header validation, adoption, catalogue-tier check, seeded transition state, ledger hash | `AngeeResource:56`, `after_init_instance:174`, `after_save_instance:205`, `_row_content_hash:398`, `_adoption_fields:522`, `_unique_adoption_field:666`, `_validate_headers:753`, `_validate_catalogue_tier:772`, `build_resource:827` |
| `src/angee/resources/mixins.py` | `resource_class`, `catalogue`/`catalogue_tier(s)`, cooperative `after_resource_load`, check `angee.E014` | `ResourceLoadMixin:17`, `is_catalogue_model:48`, `check:67`, `after_resource_load:97` |
| `src/angee/resources/models.py`, `managers.py` | the ledger `(source_addon, xref)`; validate/load/diff/`load_xref`/`resolve`; demo guard, exclusions, xref collisions, merge guard | `Resource:16`; `retired_seed_keys:106`, `seeded_record_merge_guard:121`, `ResourceManager:137`, `load_addons:221`, `load_xref:248`, `_check_load_tiers:318`, `_excluded_entry_keys:536`, `_check_xref_collisions:562` |
| `src/angee/resources/grants.py`, `management/commands/resources.py` | `kind = "grants"` rows into REBAC tuples, idempotent by tuple key; the `resources validate\|load\|diff` command and tier selection | `materialize_grant_groups:61`, docstring `:1-12`; `Command:17` |
| `src/angee/resources/build.py`, `django_compose.migrations` | catalogue projection onto runtime models; the populated-table drop guard and its retirement exemption | `CatalogueIntegration:18`, `retired_primary_keys:38`; `DropGuardAutodetector:624` |

## Reuse first

- The loader is django-import-export + tablib composed by `angee.resources` ([stack](../../../docs/stack.md#backend),
  `django-import-export` row): never a `loaddata` fixture, a row-creating command or a `post_migrate` seeder.
- Identity is the ledger's `(source_addon, xref)`; relations name rows `<addon label or name>.<xref>`
  (`widgets.py` `split_xref:288`, `resolve_xref:223`).
- Runtime code reaches a seeded row with `Resource.objects.resolve(handle, model=…)` (`managers.py:146`)
  or re-imports one with `load_xref` (`managers.py:248`); tests take the concrete ledger from
  `angee.resources.testing` (`src/angee/resources/testing/__init__.py:1-12`).
- Shared catalogues exist: `integrate.Vendor` (`src/angee/integrate/models.py:1464`), `money.Currency`
  (`src/angee/money/models.py:80`); contribute rows with `adopt`.
- Owner-verb adapters exist: `WorkflowDefinitionResource`, `TriggerResource` (`src/angee/workflows/resources.py:68,124`);
  non-row declarations use `ResourceEntryKind` (`src/angee/resources/kinds.py:16`).

## Steps

1. **Pick the tier and bind catalogue models to it.** `master` = reference data every
   deployment carries; `install` = rows the deployment needs to function (rules, grants);
   `demo` = sample rows, loaded only under `DEBUG` or `--allow-non-dev` (`managers.py:318`).
   A model several addons seed declares its tier once (`money/models.py:80-106`):

   ```python
   class Currency(ResourceLoadMixin, ArchiveMixin, AngeeDataModel, concrete=True):
       catalogue = True
       catalogue_tier = "master"          # or catalogue_tiers = ("master", "install")
       code = models.CharField(max_length=3, unique=True)

       class Meta:
           abstract = True                # a composition source must be abstract (model_composition.py:178-181)
           rebac_resource_type = "money/currency"
   ```
   **Owner rule:** the model owns its tier, read without inheritance (`mixins.py:48`); a
   manifest that disagrees fails at load (`loader.py:772`)
   ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).

2. **Write `resources/<tier>/NNN_<label>.<model>.yaml`.** `NNN` orders siblings; the loader
   infers the model from `<label>.<model>` (`entries.py:406`) or from `_meta.model`. Use the
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
   (`entries.py:536-539`). If a field is guarded by `StateTransitions` → seed the initial
   value only ([seeded transition state](../../../docs/backend/guidelines.md#seeded-transition-state)).
   **Owner rule:** row keys are model fields cleaned by the native import; primary keys and
   unknown headers are refused (`loader.py:753`), entry and file labels must agree
   (`entries.py:498`) ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).

3. **Declare the entry in `pyproject.toml`.** A bare path suffices for an independent file.

   ```toml
   [tool.angee.resources]                                          # money/pyproject.toml:8-12
   master = [{ path = "master/010_money.currency.yaml", adopt = "code" }]
   demo = [
     { path = "demo/020_money.currencyrate.yaml", depends_on = "master/010_money.currency.yaml" },
   ]
   ```
   If a row's natural key may already exist (shared catalogue, operator-created row) →
   `adopt = "<unique field>"` or `adopt = ["f1", "f2"]`, the exact field set of a model-owned
   `unique`, `unique_together` or non-expression `UniqueConstraint` (`loader.py:666`;
   `notes/pyproject.toml:34` adopts `["user", "name"]` through `Credential`'s conditional
   constraint). If a relation names another addon's seed → `depends_on = "<label>:resources/…"`
   in a tier at or below yours (`entries.py:566`). **Owner rule:** the manifest selects files
   and keys; the model owns uniqueness, so no field is adoptable without it
   ([addon declarations](../../../docs/composer.md#addon-declarations)).

4. **Grant relations with `kind = "grants"`.** Declare the entry as
   `{ path = "demo/030_iam.admin_grant.yaml", kind = "grants" }` (`iam/pyproject.toml:24`),
   ordered after the rows it names. A row xref resolves through the ledger, a literal is
   `<ns>/type:<id>[#relation]`, `*` is the anonymous subject.

   ```yaml
   rows:
     - resource: angee/role:admin           # iam/resources/demo/030_iam.admin_grant.yaml
       relation: member
       subject: angee.iam.user_admin
   ```
   **Owner rule:** the loaded row owns its REBAC type and the native writer validates the
   tuple (`grants.py:1-12`); a grants file declares no model (`entries.py:382`).

5. **Persist through an owner verb only when the model requires it.** Set `resource_class`
   on a `ResourceLoadMixin` model to an `AngeeResource` subclass (`loader.py:827` refuses
   anything else). The adapter keeps the native hooks and hands the saved identity back to
   the row instance, because `after_save_instance` ledgers that instance (`loader.py:205`):

   ```python
   class Workflow(ResourceLoadMixin, OwnerMixin, HistoryMixin, AngeeDataModel):  # workflows/models.py:145
       resource_class = WorkflowDefinitionResource                               # :155

   class WorkflowDefinitionResource(AngeeResource):                             # workflows/resources.py:68
       def save_instance(self, instance, is_create, row, **kwargs):            # :99
           self.before_save_instance(instance, row, **kwargs)
           installed = type(instance).objects.install_definition(key=instance.key, ..., actor=kwargs.get("actor"))
           for field in instance._meta.concrete_fields:                        # saved fields -> row instance
               setattr(instance, field.attname, getattr(installed, field.attname))
           instance._state.adding = False                                      # saved state -> row instance
           instance._state.db = installed._state.db
           self.after_save_instance(instance, row, **kwargs)
   ```
   For batch work, override `after_resource_load` and call `super()` once (`src/angee/dashboards/models.py:792`).
   **Owner rule:** the adapter delegates to the manager and keeps the native ledger hooks, adding
   no importer or ledger of its own ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).

6. **Retire a seeded model.** If the table holds only rows this addon's files seeded →
   `retired_models = ["messaging.ActivityType"]` under `[tool.angee.resources]` (`src/angee/messaging/pyproject.toml:39`);
   otherwise → a data migration ([migration history](../../../docs/backend/guidelines.md#migration-history)).
   **Owner rule:** the drop guard exempts only this addon's ledgered keys (`build.py:38`, `managers.py:106`);
   removing a manifest entry retires nothing ([addon declarations](../../../docs/composer.md#addon-declarations)).

7. **Load and reload.** `resources load` applies `master` and `install`; `--include-demo`
   adds `demo`; a selected tier brings its prerequisites (`tiers.py:32`). A deployment that
   must skip one entry sets `ANGEE_RESOURCE_EXCLUDED_ENTRIES` (`managers.py:536`).
   **Owner rule:** idempotency is the ledger's `(source_addon, xref)` plus the row content
   hash (`models.py:66-67`, `loader.py:398`); an unchanged replay touches no row
   ([resource loading](../../../docs/backend/guidelines.md#resource-loading)).

## Do not

- A row that carries `id`, a pk or a public sqid: the ledger's xref owns identity, so the header check refuses them (`loader.py:753`).
- A `loaddata` fixture, seeding command or `post_migrate` hook for rows the loader can import: the native import lifecycle already owns them ([stack](../../../docs/stack.md#backend), `django-import-export` row).
- A resource file without a `[tool.angee.resources]` entry: the manifest selects files, so an unlisted file loads nothing ([resource pitfalls](references/pitfalls.md)).
- Promoting a demo row to `install`, or editing another addon's seed, to remove a permissive row: tiers are additive per addon, so the deployment excludes the entry instead ([demo tiers are additive](../../../docs/backend/guidelines.md#resource-loading)).
- Selecting a seeded row by display name when it has an xref or stable key: the ledger owns the lookup ([seeded rows carry a stable key](references/pitfalls.md)).
- Seeding fields that `StateTransitions` writes (`workspace`, `runtime_status`, `last_error`): the transition owns them and the loader does not protect them ([seeded transition state](../../../docs/backend/guidelines.md#seeded-transition-state)).
- A catalogue tier on `AppConfig` or a second registry, or `runtime/` model imports to resolve a seed: the source model owns the tier and the ledger owns resolution ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).
- Caveated REBAC subjects behind a grant: actor-scoped querysets have no caveat context, so `angee.E024` rejects them (`src/angee/base/checks.py:37`) and grants are caveat-free (`grants.py:8`).

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
  skips `after_resource_load` (`managers.py:406-407`), omits `demo` without `--include-demo`, and counts zero rows for an unlisted file.



## Pitfalls

- A master entry cannot `depends_on` a demo entry: the target is outside the selected tiers and the load fails (`entries.py:566`, `tiers.py:32`).
- One xref per addon across every file and tier; a repeat fails the whole load (`managers.py:562`).
- `adopt` on a field without model-owned uniqueness fails at load, not at `check`; a composite key must equal a constraint's exact field set (`loader.py:666`, `loader.py:730`).
- A changed seeded `status` is validated, then discarded on update; only a new row takes it ([seeded transition state](../../../docs/backend/guidelines.md#seeded-transition-state), `loader.py:174`).
- Merging a seeded record away is refused because the next load would recreate it (`managers.py:121`).
- A flat-form row whose model has a `model` field is rejected as reserved; use the nested form (`entries.py:536-539`).

## Read next

- [Resource pitfalls](references/pitfalls.md)
- [Resource loading](../../../docs/backend/guidelines.md#resource-loading), [Composition owners](../../../docs/backend/guidelines.md#composition-owners), [Migration history](../../../docs/backend/guidelines.md#migration-history)
- [Addon declarations](../../../docs/composer.md#addon-declarations), [Addon-owned runtime migrations](../../../docs/composer.md#addon-owned-runtime-migrations)
- [Composition and schema](../../../docs/checks.md#composition-and-schema), [Glossary: Resource file](../../../docs/glossary.md#backend); skills `angee-addon`, `angee-models`, `angee-permissions`, `angee-tests`, `slice`
