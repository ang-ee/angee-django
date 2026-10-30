---
name: provenance-attribution
description: "Opts a model into history and statements in an Angee addon: HistoryMixin and captured managers, changeset boundaries and actor attribution (entrypoints, Celery Task, fresh writes, claims), StatementRecords aspects and sources, DerivedFrom evidence, the no-provenance foundation rule, history tests."
---

# Provenance Attribution

The reference example is `src/angee/tags` (smallest captured model) with `tests/statement_models.py`
for statements. `angee.provenance` owns capture, attribution and serving; an addon declares which rows it keeps.

## Use when

- "Keep history / audit trail / revert for `<Model>`."
- "Record who changed `<Model>` and why" or "attribute a task's / seed's writes."
- "Add statements / aspects / source precedence to `<Model>`" or "make `<Principal>` a statement source."
- "Retain the evidence `<Record>` was derived from."
- Not for ownership or `created_by` columns (`angee-models`, `AuditMixin`) or timeline pages (`angee-pages`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/models.py` | `HistoryMixin` composition, captured manager, `history_revert_permission`, `history_relations`, `statements` declaration | `src/angee/tags/models.py:58-77`, `tests/statement_models.py:16` |
| `<addon>/pyproject.toml` | `[project].dependencies = [..., "angee-provenance>=3,<4"]` | `src/angee/tags/pyproject.toml` |
| `src/angee/provenance/contrib/angee/history.py` | `HistoryMixin` (:346), `ModelHistory` (:97), `HistoryDocumentMixin` (:50), `record_history` (:86) | docstrings |
| `src/angee/provenance/contrib/angee/managers.py`, `querysets.py` | `AngeeHistoryManager` (:20), `AngeeHistoryUnscopedManager` (:24), `AngeeHistoryMergeableUnscopedManager` (:33); `AngeeHistoryQuerySet` (:16) | docstrings |
| `src/angee/provenance/history.py`, `querysets.py`, `context.py` | native capture without Angee policy: `ModelHistory` (:132), `HistoryMixin` (:438); `changeset()` (:334), `current_changeset` (:224), `ChangesetRequired` (:28) | [README](../../README.md) |
| `src/angee/provenance/models.py`, `managers.py` | `Changeset` (:102) with `ambient_attribution` (:188), `revert` (:370), `document` (:349); `Rule` (:78), `Seed` (:90); `ChangesetQuerySet.touching` (:30), `ChangesetManager.claim` (:134) | docstrings |
| `src/angee/provenance/statements.py` | `StatementRecords` (:1424), `Aspect` (:730), `PairAspect` (:1624), `StatementsMixin` (:798), `StatementSourceMixin` (:678), `StatementLinkMixin` (:710), `SourceKind` (:664) | module docstring |
| `src/angee/provenance/tasks.py`, `middleware.py`, `contrib/angee/context.py` | the entrypoint boundaries: `Task` (:12), `ChangesetMiddleware`, `management_context` (:25), `merge_context` (:34) | [README: Boundaries](../../README.md#boundaries) |
| `src/angee/provenance/evidence.py`, `src/angee/base/models/derived.py` | `EvidenceReference` (:38), `EvidenceFact` (:28), `FactAuthority` (:20), `readable_records` (:127); `DerivedFrom` (:13) | docstrings |
| `src/angee/provenance/testing/` | `captured_changeset` autouse fixture (`fixtures.py:21`), concrete `Changeset`/`Rule`/`Seed` (`models.py`) | [source-addon test models](../../../../../docs/checks.md#source-addon-test-models) |

## Reuse first

- `HistoryMixin` from `angee.provenance.contrib.angee.history` (never the native `angee.provenance.history` one or
  `simple_history.models.HistoricalRecords` directly) and the three captured managers above.
- `AuditMixin` (`src/angee/base/models/audit.py`) for `created_by`/`updated_by` stamps; `OwnerMixin` for transferable
  access; `TrashMixin` so a created-row revert can use retained removal.
- The entrypoint boundaries in the README table: request, connection, task, command, bridge session, test.
- `Changeset.objects.claim(scope, key, fingerprint=)` for idempotent creation; `merge_context` for merges.
- `angee.base.models.derived.DerivedFrom` for evidence edges; `readable_records`/`readable_evidence` for admission and display.
- Statement declarations with donors and links: `src/angee/parties/models.py:111`; the `UserSource` donor
  (`src/angee/provenance/contrib/iam/models.py:12`) as the pattern for principals in addons that must not depend on provenance.
- Upstream: django-simple-history (`diff_against`, `bulk_history_create`), `rebac` `actor_context`/`system_context`, Celery `DjangoTask`
  — [Stack](../../../../../docs/stack.md#backend) rows 67, 76, 79.

## Steps

1. **Declare the dependency.** `pyproject.toml` `[project].dependencies` adds `angee-provenance` (it brings `simple_history`).
   **Owner rule:** only addons that opt in depend on provenance; `angee.base` and the generic compiler never do, so a foundation or IAM fact
   that provenance needs arrives as a provenance-owned donor (`contrib/iam/models.py:12`;
   [README](../../README.md), [Package layering](../../../../../docs/backend/guidelines.md#package-layering)).

2. **Compose `HistoryMixin` and the captured managers.**

   ```python
   class TagQuerySet[TagT: Tag](MergeableQuerySet[TagT], ArchiveQuerySet[TagT], AngeeHistoryQuerySet[TagT]):
       """Archive read scopes layered over the REBAC-scoped tag queryset."""

   TagManager = AngeeHistoryManager.from_queryset(TagQuerySet)

   class Tag(HistoryMixin, MergeableMixin, ArchiveMixin, AngeeDataModel, concrete=True):
       sqid_prefix = "tag_"
       _angee_base = AngeeHistoryMergeableUnscopedManager()   # AngeeHistoryUnscopedManager() without MergeableMixin
       objects = TagManager()
   ```

   Without a custom queryset the mixin's defaults already bind `objects` and `_angee_base` (`history.py:349-350`).
   Vary only `history_revert_permission` (default `write`) and `history_relations` for owned reverse-FK document parts
   (`src/angee/projects/models.py:515`). **Owner rule:** the mixin owns capture, redaction-safe snapshots, revert
   admission and the table inventory; a model names only what varies ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives)).

3. **Let migrations follow the swappable storage.** Django's `makemigrations` on the built runtime owns the history tables;
   a hand-written migration that references the changeset FK depends on
   `migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)` ([README](../../README.md)). **Owner rule:**
   [addon-owned runtime migrations](../../../../../docs/composer.md#addon-owned-runtime-migrations).

4. **Write under the entrypoint's boundary; attribute, and open only a distinct logical write.** Attribution is ambient: the
   changeset projects REBAC's current actor and sudo reason (`Changeset.ambient_attribution`, `models.py:188`), so domain code
   sets the actor with `actor_context`/`system_context(reason="<label>.<verb>")` and writes. A deliberately separate logical
   write (merge, revert, seed, an MCP tool call, a bridge session or stream pull) opens its own, nested inside the entrypoint's:

   ```python
   with changeset(fresh=True, activity="system", change_reason="integrate.stream.advance", origin=stream):
       ...
   ```

   Machine reasons use the `a.b` form so presentation stays silent (`MACHINE_REASON`, `models.py:61,215`); a human
   sentence passes through. Keyed creation claims first: `Changeset.objects.claim(scope, key, fingerprint=...)`.
   **Owner rule:** entrypoints own boundaries, one per unit of work; strict mode refuses a captured write without one
   ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives), "Every captured write…").

5. **Attribute Celery work through the task class.** Provenance sets `ANGEE_CELERY_TASK_CLASS = angee.provenance.tasks:Task`
   (`autoconfig.py:25`), which opens `task.<name>` with `activity="system"` per run (`tasks.py:12`). A task declares a stable
   name and binds its principal before captured writes:

   ```python
   @shared_task(name="agents.run_session")
   def run_session(session_id: int) -> None:
       run_next_turn(session_id)            # inside: with actor_context(principal): ...
   ```

   (`src/angee/agents/tasks.py:8`, `sessions.py:57-80`). **Owner rule:** jobs owns transport and retries, provenance the
   attribution ([Stack](../../../../../docs/stack.md#backend), Celery row); a task body nests its own `changeset(fresh=True,
   activity="pull", ...)` only when the run is a distinct logical pull (`src/angee/integrate/session_runner.py:27-45`;
   [README: Boundaries](../../README.md#boundaries) "Separate logical writes"; `jobs-tasks` step 7).

6. **Declare statements when sources disagree about a row.** The canonical model composes `StatementsMixin, HistoryMixin`
   and declares aspects; each principal model composes `StatementSourceMixin` with a `source_kind`.

   ```python
   class StatementSubject(StatementsMixin, HistoryMixin, AngeeDataModel):
       label = models.CharField(max_length=100, blank=True)
       detail = models.TextField(blank=True)
       statements = StatementRecords(
           aspects={
               "identity": Aspect(fields=("label",)),
               "membership": Aspect(fields=("detail",),
                                    member={"member": models.ForeignKey("base.StatementSubject", on_delete=models.PROTECT)}),
           },
           link=None,
       )
       same_as = PairAspect()
   ```

   `Aspect.precedence` orders `SourceKind`s; `link="integrate.RecordLink"` and `sqid_prefix` belong to the subject source's
   declaration, donors contribute aspects only (`statements.py:1484`); `model_name=` when `<Model>Statement` is taken.
   Writes go through `instance.statements.set(...)`. **Owner rule:** [Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives),
   statements paragraphs; [Glossary: Statement](../../../../../docs/glossary.md#backend).

7. **Retain derivation evidence.** An evidence owner declares a `DerivedFrom` child (`DecisionRecord`,
   `src/angee/decisions/models.py:95`; `ExtractionSource`, `src/angee/extraction/models.py:514`) and admits sources
   with `readable_records(refs, actors)` (standing read for every participant); frozen values travel as `EvidenceFact`
   with a `FactAuthority`. **Owner rule:** [Glossary: Derived-from evidence](../../../../../docs/glossary.md#workflows);
   edges never grant access to their targets.

8. **Serve nothing new.** `history(model, id)` and `revert_changeset` live in provenance's `console` schema, resource metadata
   marks `history: true`, and the web fragment renders the timeline and drill-in ([README: History surface](../../README.md#history-surface)).
   **Owner rule:** history reads follow the record's `read` gate and field gates; an addon exposes no history type of its own.

9. **Test history and statements.** Hosts list `angee.provenance.testing` and register `angee.provenance.testing.fixtures`
   (autouse `captured_changeset`); a test that exercises a production boundary is marked `entrypoint_boundaries` and opens
   `changeset(fresh=True)` itself (`tests/test_provenance_context.py:16-24`). Assert through `row.history.model.objects`,
   `Changeset.objects.touching(record)` and `instance.statements` (`tests/test_history.py`, `tests/test_statement_records.py`).
   Statement sources need parties' install-tier `Rule` principals (`statement_sources` fixture).
   **Owner rule:** [source-addon test models](../../../../../docs/checks.md#source-addon-test-models).

## Do not

- `HistoricalRecords()` or `simple_history.register()` on an addon model that keeps history: `HistoryMixin`/`ModelHistory` own capture and its Angee policy ([Stack](../../../../../docs/stack.md#backend), django-simple-history row).
- An `angee.provenance` import in `angee.base`, the generic compiler, IAM or another foundation-level owner: a provenance-owned `extends` donor (`contrib/iam/models.py:12`; [Package layering](../../../../../docs/backend/guidelines.md#package-layering)).
- `changeset(fresh=True)` in a resolver, model method or manager verb that is not a separate logical write: the entrypoint boundary ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives)).
- A `changed_by`/`change_reason` column or a parallel audit-log table on the model: `Changeset` attribution plus `AuditMixin` stamps ([Glossary: Changeset](../../../../../docs/glossary.md#backend)).
- `system_context` wrapped around a write to make it attributable or revertable: attribution never grants ([README](../../README.md), "No captured write grants an authorization bypass").
- A custom Celery base class or publish hook for attribution: `ANGEE_CELERY_TASK_CLASS` (`autoconfig.py:25`).
- Client-key or fingerprint columns on product rows for idempotent creation: `Changeset.objects.claim` ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives), "Idempotent creation").
- Bulk updates on `<Model>Statement` tables or an own GraphQL type over `instance.history`: `statements.set()`/status verbs and the `history` query ([README: History surface](../../README.md#history-surface)).
- `GeneratedField` or counters expected in history: excluded by the mixin ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives), "`HistoryMixin` excludes `GeneratedField`").

## Verify

Done means: the generated runtime model exposes `history` (and `statements`) descriptors; a save in a test creates a
historical row whose changeset is `current_changeset()`; `Changeset.objects.touching(record)` lists it; `angee.E038`
(middleware order, `checks.py:34`) and the base `angee.E*` checks are clean; the fresh migration adds only the history and
statement tables.

- In a lane (jj workspace: no host, database or JS install): focused pytest (`tests/test_history.py`, `tests/test_statement_records.py`,
  the addon module), ruff, mypy on touched files (the `angee.provenance.typing` plugin is in the mypy config), `tests/test_layering.py`
  — [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: build, migration autodetection, permission sync, the native host proof
  (`tests/test_provenance_native.py`), the console history drill-in — [composition and schema](../../../../../docs/checks.md#composition-and-schema).
- Loop: [slice](../../../../../.agents/skills/slice/SKILL.md#workflow). False greens: with `REBAC_STRICT_MODE` off a missing
  boundary is never refused (`Changeset.context_required`, `models.py:195`); the generic base test plugin installs no
  boundary, only the provenance fixtures do ([source-addon test models](../../../../../docs/checks.md#source-addon-test-models)).

## Pitfalls

- A queryset update writing only excluded columns records no version; an MTI child save records the child's history only ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives), "`HistoryMixin` excludes `GeneratedField`").
- One changeset row per writing thread and actor: a pool thread needs `copy_context().run` or a `process=True` boundary ([README: Boundaries](../../README.md#boundaries)).
- Management commands need the native launcher and provenance's `COMPOSE_COMMAND_CONTEXT` contribution; re-render an obsolete entrypoint ([README: Boundaries](../../README.md#boundaries)).
- `history_relations` are document parts with their own capture and read gates, not arbitrary references (`history.py:357-361`).
- Revert refuses any later touch of a row and reuses trash for created subjects; without `TrashMixin` retained references block removal ([Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives)).
- A seed load, merge, revert, MCP tool call or bridge session is a distinct logical write with its own `fresh=True` boundary nested inside the entrypoint's; a write that is not a distinct unit stays under the entrypoint boundary ([README: Boundaries](../../README.md#boundaries) "Separate logical writes").

## Read next

- [Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives), [Migration history](../../../../../docs/backend/guidelines.md#migration-history), [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)
- [Explicit migration retirement policy](../../../../../docs/backend/guidelines.md#migration-history), [Stack](../../../../../docs/stack.md#backend)
- [Glossary: Backend](../../../../../docs/glossary.md#backend) (Principal, Actor, Changeset, Statement, Aspect, Survivorship)
- [Provenance README](../../README.md), [Parties README](../../../parties/README.md)
- Skills: `angee-models`, `angee-permissions`, `angee-resources`, `angee-tests`, `slice`
