---
name: angee-models
description: "Build or change a Django model in an Angee addon: base and mixins, fields (StateField, SqidField, ImplClassField, FractionalRankField, EncryptedField), managers and querysets, constraints, transitions, donors and MTI children via runtime/extends, and the checks that enforce it."
---

# Angee Models

## Use when

- "Add a model / table / row type to the `<addon>` addon."
- "Make `<Model>` trashable / archivable / mergeable / owned / hierarchical / ordered / append-only."
- "Add a status/state machine, a backend selector, or an encrypted column to `<Model>`."
- "Extend another addon's model with fields" or "add a concrete kind of `<Parent>`."
- "Add a manager method / queryset scope / constraint / index to `<Model>`."

For *which* mixin or base fits a fact, use `angee-design`'s owner index. This
skill shows how to compose the chosen owner correctly.

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/models.py` | abstract source models, their fields, methods, managers, `Meta`, composition markers | `addons/angee/tags/models.py:74-100`, `examples/addons/example/notes/models.py:22-71` |
| `<addon>/managers.py` (optional) | row-set APIs once they outgrow `models.py` | `addons/angee/workflows/managers.py` |
| `<addon>/addon.toml` | addon identity, `depends_on`, resources; never field facts | `addons/angee/tags/addon.toml` |
| `<addon>/permissions.zed` | the `rebac_resource_type` definition the model names | `examples/addons/example/notes/permissions.zed` |
| `<addon>/autoconfig.py` | `Base.registry_setting` mapping for `ImplClassField`; native addon checks for rowless contracts | `addons/angee/storage/autoconfig.py:52-58` |
| `addons/angee/base/models/base.py` | `AngeeModel`, `AngeeDataModel`, `runtime`/`extends`/`rebac_grantable`, `delete_blocker`, `lock_for_delete` | `:39`, `:88`, `:103`, `:112` |
| `addons/angee/base/models/*.py` | one capability mixin per module (trash, merge, ownership, audit, hierarchy, stages, transitions, locks, refs, append_only, implementations, inputs, persistence) | module docstrings |
| `addons/angee/base/fields/__init__.py` | the foundation field roster | `fields/state.py`, `fields/identity.py`, `fields/implementation.py`, `fields/ordering.py`, `fields/encrypted.py`, `fields/text.py` |
| `addons/angee/base/managers.py`, `querysets.py` | `AngeeManager`/`AngeeQuerySet`, unscoped managers, `ClosedUpdateQuerySet`, `lock_if_supported`, `from_public_id` | `managers.py:13-34`, `querysets.py:159-240` |
| `angee/compose/model_composition.py` | interpretation of `runtime`, `extends`, `requires_parent`; donor order, field and manager collisions | `:141-208`, `:284-339` |
| `runtime/<app_label>/` | generated concrete models and migrations — output, never edited | `docs/composer.md#runtime-build-and-import` |

## Reuse first

Search these before writing a column, guard, or helper:

- Bases: `AngeeDataModel` (public rows), `AngeeModel` (no public id), `AppendOnlyModel` (`addons/angee/base/models/append_only.py:108`).
- Mixins: `ArchiveMixin`/`TrashMixin` (`trash.py`), `MergeableMixin` (`merge.py`), `OwnerMixin`/`ItemOwnershipMixin` (`ownership.py`), `AuditMixin` (`audit.py`), `HierarchyMixin` (`hierarchy.py`), `Stage`/`StagedModelMixin` (`stages.py`), `StateTransitions`/`transition` (`transitions.py:178`, `:100`), `ImmutableFieldsMixin`/`OptimisticLockMixin` (`locks.py`), `RecordRefMixin` (`refs.py`), `ImplDefaultsMixin` (`implementations.py:23`), `InputExtensionsMixin` (`inputs.py:9`), `role_anchor` (`roles.py:14`), `TimestampMixin` (already in `AngeeModel`).
- Fields: `angee.base.fields` — `StateField`, `SqidField` (via `sqid_prefix`), `ImplClassField`, `FractionalRankField`, `EncryptedField`, `ModelLabelField`, `DiagnosticTextField`, `ColorField`, `ToneField`; `angee.base.fields.relations.RetainedGenericRelation` (`relations.py:11`).
- Querysets: `AngeeQuerySet` methods (`from_public_id`, `lock_if_supported`, `locked_get`, `keyset_page`), `MergeableQuerySet`, `ArchiveQuerySet`, `TrashQuerySet`, `HierarchyQuerySet`, `AppendOnlyQuerySet`, `ClosedUpdateQuerySet`; history variants in `addons/angee/provenance/contrib/angee/`.
- Elevated reads: `Model.system_queryset(lock=...)`, a classmethod every `PersistenceMixin` model carries (`addons/angee/base/models/persistence.py:48-49`).
- Cross-addon donors you may compose instead of re-declaring: `angee.tags.models.TaggedModel`, `angee.messaging.models.ThreadedModelMixin`, `angee.resources.mixins.ResourceLoadMixin`, `angee.provenance.contrib.angee.history.HistoryMixin`.
- Upstream: Django `Meta`, constraints, `Manager.from_queryset`, signals, `transaction.atomic`; `rebac` (`generic_target`, `RebacModelBase`, `system_context`); `django-choices-field`; `django-sqids`; `django-simple-history`. See `docs/stack.md#backend`.

## Steps

1. **Declare the source model** in `<addon>/models.py`. Abstract, `runtime = True`, a `sqid_prefix`, and Django `Meta` only.

   ```python
   class Tag(HistoryMixin, MergeableMixin, ArchiveMixin, AngeeDataModel):
       """One label in a vocabulary shared by every non-anonymous actor."""

       runtime = True
       sqid_prefix = "tag_"

       name = models.CharField(max_length=128)
       color = ColorField(max_length=32, blank=True, default="")

       objects = TagManager()

       class Meta:
           abstract = True
           ordering = ("name", "sqid")
           rebac_resource_type = "tags/tag"
   ```

   If the row has no public API identity → `AngeeModel`. If rows are retained evidence → `AppendOnlyModel` before the base (step 5). The app label is the addon package's Django default (`docs/backend/guidelines.md#django-native-rule`); FK strings across addons hard-code it, so choose it once.
   **Owner rule:** an addon declaration fact (`depends_on`, resources) goes in `addon.toml` and a record fact on the model class (`docs/guidelines.md#put-behavior-on-the-owning-object`); `Meta` carries an Angee fact only when the owning library reads it there, as `rebac_resource_type` is (`docs/glossary.md`, `Meta`).

2. **Compose foundation mixins before the base, capability-first.** Mixins precede `AngeeDataModel`; cross-addon donors such as `TaggedModel` precede the mixins that depend on them.

   ```python
   class Note(RecordChangedOptIn, OwnerMixin, AuditMixin, ThreadedModelMixin, HistoryMixin, AngeeDataModel):
       runtime = True
       rebac_grantable = {"reader": "share", "editor": "share"}
       sqid_prefix = "nte_"
   ```

   Archive and trash are different facts: `ArchiveMixin` hides from pickers, `TrashMixin` removes with who/when/reason and needs the Zed `trashed` arm. If the model carries a polymorphic edge or a reverse `GenericRelation` (`RecordRefMixin`, `TaggedModel`, `ThreadedModelMixin`) → compose it on the topmost REBAC-typed MTI ancestor, never on a child (placement invariant, `addons/angee/base/models/refs.py:1-11`). If one relation is called `owner` → `OwnerMixin` plus `relation owner: auth/user // rebac:field=owner`.
   **Owner rule:** when a foundation mixin already owns the capability, it owns the column, its queryset scope, its check and its verb, and the model names only the fact that varies (`sqid_prefix`, `hierarchy_scope_fields`, `immutable_fields`, `merge_policy`); a gap is fixed in the mixin, not beside it (`AGENTS.md#constitution`, "Compose reusable components").

3. **Declare fields from the foundation roster.**

   ```python
   class Status(models.TextChoices):
       DRAFT = "draft", "Draft"
       ACTIVE = "active", "Active"

   status = StateField(choices_enum=Status, default=Status.DRAFT)
   kind = StateField(choices_enum=Kind, null=True, blank=True)      # optional enum: NULL, never ""
   backend_class = ImplClassField(StorageBackend)                    # key, resolved via registry setting
   client_secret = EncryptedField(blank=True)                        # key bound to label_lower + field name
   sort_order = FractionalRankField()                                # NOT NULL + UniqueConstraint(context, rank)
   model_label = ModelLabelField(max_length=100)                     # "app_label.ModelName"
   failure_reason = DiagnosticTextField(blank=True, default="")
   ```

   If values vary by *behaviour* with the same persisted shape → `ImplClassField(Base)`; declare its keys under `Base.registry_setting` (the native field check validates every entry) in the addon's `autoconfig.py`, with a noop default so the registry is never empty (`addons/angee/storage/autoconfig.py:52-58`). If rows are mutually exclusive *kinds* with their own columns → an MTI child (step 8), not a discriminator column. Manual ordering: declare the rank plus `UniqueConstraint(fields=(<context>, "sort_order"))`; the field allocates appends, the owner reranks under its own lock (`addons/angee/projects/models.py:895-914`, `docs/backend/guidelines.md#rules`).
   **Owner rule:** value coercion and validation live on the field class (`docs/backend/guidelines.md#django-native-rule`, "Value coercion lives on fields"); a caller branches on a field's type or re-validates its value only when the field has no hook for that rule, and then the fix is the hook.

4. **Guard state changes with transitions, on the model.** Every `source → target` pair a decorator declares must be an edge of the graph, or class construction fails (`addons/angee/base/models/transitions.py:362`).

   ```python
   lifecycle_transitions = StateTransitions(
       lifecycle,
       {Lifecycle.DISCONNECTED: [Lifecycle.CONNECTED],
        Lifecycle.CONNECTED: [Lifecycle.PAUSED, Lifecycle.DISCONNECTED],
        Lifecycle.PAUSED: [Lifecycle.CONNECTED, Lifecycle.DISCONNECTED]},
   )

   @transition(lifecycle, source=[Lifecycle.DISCONNECTED, Lifecycle.PAUSED],
               target=Lifecycle.CONNECTED, on_success=_save_connected)
   def connect(self, *, credential: Any = _UNSET) -> None:
       ...
   ```

   Guards are opt-in: a `StateField` without a `StateTransitions` declaration is an ordinary column. The body runs inside the primitive's transaction, so defer non-DB effects to `transaction.on_commit`. `policy_setting=` lets a deployment enable or deny edges through composed settings; a `policy=` edge is then validated at call time instead of at class build (`transitions.py:363-369`). Reference: `addons/angee/integrate/models.py:2183-2195`, `:2283-2289`, `_save_connected` at `:1689`.
   **Owner rule:** once a `StateTransitions` guards a field, the decorated method is the verb and direct assignment raises (`transitions.py:491`); resolvers, actions and commands call the verb. `force_state`/`write_state` are the reasoned bypass for recovery, never a shortcut (`transitions.py:318`, `:455`).

5. **Declare the manager through a queryset.** Capability querysets first, then the history/REBAC base; the manager is `from_queryset`.

   ```python
   class TagQuerySet[TagT: Tag](MergeableQuerySet[TagT], ArchiveQuerySet[TagT], AngeeHistoryQuerySet[TagT]):
       """Archive read scopes layered over the REBAC-scoped tag queryset."""

   TagManager = AngeeHistoryManager.from_queryset(TagQuerySet)

   class Tag(HistoryMixin, MergeableMixin, ArchiveMixin, AngeeDataModel):
       _angee_base = AngeeHistoryMergeableUnscopedManager()   # mergeable + history need the matching base manager
       objects = TagManager()
   ```

   `objects` is actor-scoped; `Model.system_queryset(lock=("self",))` is the elevated read for system work (`addons/angee/base/models/persistence.py:48-49`); `lock_if_supported()` names lock intent on both (`querysets.py:169`). If rows are retained → `AppendOnlyModel` and a default manager whose queryset composes `AppendOnlyQuerySet` (`angee.E034`); expose exact writes as `owner_update`/`owner_bulk_create` methods. If `HierarchyMixin` → compose `HierarchyQuerySet` first on every manager (`angee.E021`) and inherit `Meta(HierarchyMixin.Meta)` (`addons/angee/storage/models.py:389`, `:573`). Chainable predicates go on the queryset; factories, upserts and multi-row writes on the manager (`addons/angee/tags/models.py:102-211`).
   **Owner rule:** the collection owns a row-set rule (`docs/guidelines.md#put-behavior-on-the-owning-object`, "A collection owns it"): a filter repeated across resolvers or commands is a missing queryset method, and a write that starts from the class is a manager method; a one-off predicate used by exactly one entrypoint stays there.

6. **Constraints and indexes in `Meta`**, named with the app-label prefix.

   ```python
   class Meta:
       abstract = True
       ordering = ("currency", "-date")
       rebac_resource_type = "money/rate"
       indexes = (PatternOpsIndex(fields=("name",), opclasses=("varchar_pattern_ops",)),)
       constraints = (
           models.CheckConstraint(condition=models.Q(rate__gt=0), name="%(app_label)s_rate_positive"),
           models.UniqueConstraint(fields=("currency", "date"), condition=models.Q(context_content_type__isnull=True),
                                   name="%(app_label)s_rate_currency_date"),
       )
   ```

   Reference: `addons/angee/money/models.py:359-406`. Use `PROTECT`/`RESTRICT` for retained FKs and `retained_set_null` from `audit.py:36` where a collector null must be system work.
   **Owner rule:** uniqueness and row consistency that a declarative constraint can express are `Meta.constraints` on the model; rules a constraint cannot express go to the model, manager or queryset owner plus signals, and never into a trigger, function or `RunSQL` (`docs/backend/guidelines.md#rules`, "Never a database trigger or function").

7. **Put behaviour on the owning object.** Follow the decision tree in `docs/guidelines.md#put-behavior-on-the-owning-object`: a record fact is a model method, property or field behaviour (`Currency.round`, `Note.publish`); a row-set fact is a queryset or manager method; a rule no single participant owns is the smallest addon-local service or use case that orchestrates those owners; an entrypoint parses, acquires context, dispatches and formats. A refusal to delete is `delete_blocker()` plus a `pre_delete` receiver, because `save()`/`delete()` overrides do not run on cascade or queryset paths; domain lock order is `lock_for_delete()` (`addons/angee/base/models/base.py:103`, `:112`). A `save()` override only derives columns (`Note.save` widening `update_fields`) and normalizes through the field (`CurrencyRate.save`, `addons/angee/money/models.py:408-449`).
   **Owner rule:** business-state branching on a model's fields inside a resolver, command or service means the model is missing a method, except where the branch spans owners and no participant owns it — then an addon-local orchestration function is the sanctioned shape (`docs/guidelines.md#put-behavior-on-the-owning-object`, "No participant owns a cross-owner rule").

8. **Compose across addons through the markers, not by editing the owner.** Donors and children are abstract: the composer rejects `runtime`/`extends` on a concrete class (`angee/compose/model_composition.py:178-183`).

   ```python
   class ProjectWork(models.Model):                      # same-row donor: fields land on projects.Project
       extends: ClassVar[str | None] = "projects.Project"
       hasura_readable_fields: ClassVar[tuple[str, ...]] = ("team",)
       team = models.ForeignKey("spaces.Group", null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")

       class Meta:
           abstract = True

   class Person(models.Model, metaclass=RebacModelBase):  # MTI child: own table, parent identity
       runtime = True
       extends: ClassVar[str | None] = "parties.Party"

       class Meta:
           abstract = True
           rebac_resource_type = "parties/person"

   class Bridge(models.Model, metaclass=RebacModelBase):  # capability whose methods need a parent
       requires_parent: ClassVar[str] = "integrate.Integration"

       class Meta:
           abstract = True
   ```

   References: `addons/angee/work/models.py:770-826`, `addons/angee/parties/models.py:413-461`, `addons/angee/integrate/models.py:2754-2822`. Donors and children carry only their contributed fields and behaviour; a donor that repeats a parent column is a field collision (`model_composition.py:284-311`). A child or donor that declares its own `rebac_resource_type` needs `metaclass=RebacModelBase`. The renderer emits one `donors → source → concrete parent` base order (`angee/compose/rendering.py:4`, `:39`), so a child overrides parent behaviour by ordinary MRO and cooperative hooks delegate with `super()` (`ChannelWorkflowTriggers.purge_blockers`, `TaggedModel.apply_input_extensions`). A donor consuming a GraphQL input extension declares a keyword-only parameter on `apply_input_extensions` and delegates the rest once. A factory such as `role_anchor("tags/role", name="TagRole")` must bind the module symbol it names (`model_composition.py:175`). Managers follow Django's MRO collection: every base with `_meta`, abstract or concrete, contributes its local managers, and a child with no local manager inherits the parent's default (`django/db/models/options.py:447`, `:500`), while two donors declaring the same manager name independently are rejected (`model_composition.py:337`). `Meta` options are inherited whole from abstract bases but only `ordering` and `get_latest_by` from a concrete parent (`django/db/models/base.py:192-198`), so a child re-declares other options it needs.
   **Owner rule:** dependencies point toward stable owners (`AGENTS.md#constitution`): the upstream model never names a downstream addon, and the downstream donor reaches up by label. Field, manager and permission facts stay in Python; the markers only say where the class lands (`docs/composer.md#runtime-build-and-import`).

   <details><summary>Changes under the build/host plan</summary>

   Under the planned build/host split, `runtime`, `extends`, `requires_parent` and factory markers move off the class into a model-declaration section of `addon.toml` read by hatch-angee, and deletes the class attributes; donor collision, grant-ownership and required-parent rules become `angee.base` system checks at provision. Fields, managers, `Meta` and permissions stay in Python. Until that lands, the class-attribute form above is the one the composer reads.
   </details>

9. **Wire the owners that reference the model**, each in its own file: the `rebac_resource_type` definition in `permissions.zed` (a `trashed` arm for `TrashMixin`, `owner` backed by the column, transfer and `write__owner` for `OwnerMixin`; see `angee-permissions`); the Strawberry type/resource in `schema.py`; seeds in `resources/`; a new addon's manifest and layout per `angee-addon`. Then build and author migrations from the stack root (`docs/checks.md#composition-and-schema`), never by hand under `runtime/`.
   **Owner rule:** authorization structure belongs to the zed contract beside the model (`docs/backend/guidelines.md#rebac`) and projection to `schema.py` (`docs/backend/guidelines.md#graphql-actor-and-write-contracts`); the model owns the data and the verbs both call, so a verb that exists only in a resolver is misplaced.

## Do not

Each line names the ownership violation, the condition under which it is one, the owner to compose instead, and the rule or check that owns the prohibition.

- A `CharField(choices=…)` or bare status string when the vocabulary belongs to code: the field owner is `StateField`, with `StateTransitions` once an edge needs a guard, and `Stage` rows when users name the vocabulary; `docs/backend/guidelines.md#rules`, `addons/angee/base/models/stages.py:1-9`; `StateField(blank=True)` without `null=True` fails `angee.E019` (`tests/test_layering.py:278`) and a choice value named `names`/`values`/`labels`/`choices` fails `angee.E028`.
- A `kind` column or a dotted import path when rows select behaviour: mutually exclusive kinds with their own columns are MTI children; the same persisted shape with an open set of behaviours is `ImplClassField` keyed by `Base.registry_setting` with a non-empty registry; `docs/backend/guidelines.md#rules` ("Choosing how a row selects per-variant behaviour"), `angee.E001`-`angee.E005`.
- A database trigger, function, `RunSQL`, or a `save()`/`delete()` override as the only guard when the model, manager or queryset can own the rule: overrides skip cascade and queryset paths, so the owner's method plus signals carry it; `docs/backend/guidelines.md#rules` ("Never a database trigger or function").
- A caveated subject in `permissions.zed` for a row-dependent condition when a column already carries it: a field-backed conditional relation; `angee.E024` (`addons/angee/base/checks.py:37`). A relation named `owner` without `OwnerMixin`, its transfer permission and `write__owner` gate: `angee.E045`, `angee.E022`, `angee.E027`; `docs/backend/guidelines.md#persistence-primitives`.
- A re-declared sqid column or custom public id on a row composing `AngeeDataModel`, or an `objects` whose queryset is not an `AngeeQuerySet` on an `AngeeModel`: `sqid_prefix` is the only varying fact and the canon queryset carries the actor scope; `docs/backend/guidelines.md#persistence-primitives`.
- A `HierarchyMixin` manager that does not compose `HierarchyQuerySet` first, or ordering/`update` overrides that bypass it: `angee.E021`; an ordinary manager inserting into an `AppendOnlyModel`: `AppendOnlyQuerySet` and the `owner_*` verbs, `angee.E034` (`addons/angee/base/models/append_only.py:108`).
- A guessed rank epsilon or a reused rank when `FractionalRankField` owns the context's allocation: the field's append and `get_temporary_ranks`; `docs/backend/guidelines.md#rules`.
- `runtime`/`extends` on a concrete class, a donor re-declaring parent columns, or `RecordRefMixin`/`TaggedModel`/a reverse `GenericRelation` on an MTI child: abstract donors carry only contributed fields and polymorphic edges sit on the topmost REBAC-typed ancestor; `angee/compose/model_composition.py:178-183`, `:284-311`, `addons/angee/base/models/refs.py:1-11`, `tags.E001`.
- A branch on a model's business state in a resolver, command or service when the model or its queryset owns that fact: a model or queryset method; an addon-local orchestration function stays legitimate only for a rule no participant owns; `docs/guidelines.md#put-behavior-on-the-owning-object`.
- `from <addon>.models import Model` to reach a concrete class, importing `runtime/`, or editing its generated modules and migrations: `apps.get_model` and the source declaration own those; `docs/backend/guidelines.md#rules` ("Runtime model modules, generated schemas and codegen stubs are output").

## Verify

Validators: Django system checks over the built runtime (every `angee.E*` named above), composer drift detection, migration autodetection producing exactly the intended operations, the focused pytest module for the addon, lint, types on touched files and the layering tests. Their commands and roots are owned by `docs/checks.md#python-and-composition-contracts` (source root) and `docs/checks.md#composition-and-schema` (stack root). Build-review-verify loop: `.agents/skills/slice/SKILL.md#workflow`.

Done means: system checks are clean, the generated model module under `runtime/<app_label>/` shows the donors → source → parent bases you intended, a fresh migration adds only the declared columns/constraints, the resource's GraphQL enum (for `StateField`/`ImplClassField`) renders natively, and a focused test exercises the model verb and its queryset scope through the ambient actor.

A jj workspace lane has no host, database or JS install: run the focused pytest module, lint, types on touched files and the layering tests there. Run the build, system checks, migration autodetection, permission sync, SDL and web codegen only on integrated `src`. False greens: the full suite runs with `--nomigrations` (`docs/checks.md#what-ci-actually-runs`), so a green suite says nothing about migration shape; a passing instance test does not cover cascade or queryset paths (`docs/backend/guidelines.md#rules`, "Never a database trigger or function").

## Pitfalls

- Keep migrate → permission sync → resource load in that order in every verification run; a seed's tuple write validates against the loaded schema (`docs/checks.md#composition-and-schema`).
- A donor or child that repeats parent columns is a composition error, and manager/`Meta` inheritance follows Django's MRO rules, not the emitted file order (`angee/compose/model_composition.py:284-311`; `django/db/models/options.py:447`).
- `.values_list(...).distinct()` must clear `Meta.ordering` with `.order_by()` first ([model pitfalls](references/pitfalls.md)).
- A save with `F()` expressions refreshes the instance from `RETURNING`; do not restore a locally recomputed value afterwards (same section).
- Renaming a model, app or field changes every `EncryptedField` key; plan `ANGEE_FERNET_KEYS` rotation first (same section).
- `ImplClassField` builds its enum at import: every settings module installing the addon must carry a non-empty registry (same section); a `HierarchyMixin` consumer that omits `hierarchy_scope_fields` silently accepts a parent outside its scope (same section).

## Read next

- [Model and query pitfalls](references/pitfalls.md)
- `docs/backend/guidelines.md#rules`, `#persistence-primitives`, `#direct-record-access`
- `docs/composer.md#runtime-build-and-import`, `#addon-owned-runtime-migrations`
- `docs/guidelines.md#put-behavior-on-the-owning-object`; `docs/stack.md#backend`
- `docs/glossary.md` (Source model, Model extension, Child model, Backend class, Record edge, Merge)
- `angee-design` (owner index), `angee-addon`, `angee-permissions`, `slice`, `angee-workspace`

## Sources

<!--
Docs read in full: AGENTS.md; docs/guidelines.md #put-behavior-on-the-owning-object (owner decision tree); docs/backend/guidelines.md #django-native-rule, #package-layering, #composition-owners, #rules, #models-queries-and-resources, #persistence-primitives, #migration-history, #direct-record-access, #naming; docs/composer.md #runtime-build-and-import, #addon-owned-runtime-migrations, #addon-declarations; docs/glossary.md #composition, #backend (Meta); docs/checks.md #python-and-composition-contracts, #source-addon-test-models, #agent-methodology-and-documentation, #composition-and-schema, #frontend-packages-and-addon-fragments.
Code read: addons/angee/base/models/base.py (whole); models/__init__.py; first 40 lines of every addons/angee/base/models/*.py and fields/*.py; models/append_only.py:40-150; models/persistence.py:15-61; models/transitions.py:1-60, :100-240, :319-378, :484-492; managers.py (whole); querysets.py:1-240; implementations.py:1-60; indexes.py (whole); checks.py (whole); models/refs.py:1-40; angee/compose/model_composition.py:142-334; angee/compose/rendering.py:1-60; addons/angee/base/build.py (error sites); .venv django 6.0.6: db/models/options.py:447-520, db/models/base.py:185-215.
Reference addons: addons/angee/tags/models.py, addons/angee/tags/addon.toml, addons/angee/money/models.py, examples/addons/example/notes/models.py, examples/addons/example/notes/permissions.zed (whole). Donor/child examples: addons/angee/work/models.py:155-180, :770-826, :922-945; addons/angee/parties/models.py:413-461; addons/angee/provenance/contrib/iam/models.py; addons/angee/workflows_messaging/models.py; addons/angee/integrate/models.py:1689, :2183-2195, :2283-2289, :2365-2380, :2754-2822; addons/angee/storage/models.py:526-600, storage/autoconfig.py:10-58; addons/angee/projects/models.py:890-915; tests/test_layering.py:257-301.
Outstanding discrepancy: docs/backend/guidelines.md#rules (**Enum-backed fields use `StateField`, never `CharField(choices=…)`**) links `StateField.check()` to addons/angee/base/fields/__init__.py, which only re-exports; the check is addons/angee/base/fields/state.py:133 (`angee.E019` at :155).
Doc gaps: no doc explains the donor `hasura_*_fields` declaration or the `metaclass=RebacModelBase` requirement for children/donors declaring a resource type; both derived from addons/angee/work/models.py and addons/angee/parties/models.py.
-->
