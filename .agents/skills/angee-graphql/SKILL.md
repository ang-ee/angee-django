---
name: angee-graphql
description: "Exposes a model or computed source over GraphQL and the resource UI in an Angee addon: schema.py buckets and named schemas, hasura_model_resource / hasura_pydantic_resource, type and input extensions, authored verbs, lines, changes subscriptions, SDL, codegen into @angee/gql, documents.ts."
---

# Angee GraphQL

Run `angee-design` first for the owner map; `angee-models` and
`angee-permissions` own the model and its zed. This skill owns the API surface.

## Use when

- "Expose `<Model>` over GraphQL / as a resource / in the console or public schema."
- "Add a `tag`-style mutation, an action with `ActionResult`, a delete preview."
- "Add fields to another addon's `<X>Type` or `<x>_insert_input`."
- "Editable child lines on a document", "subscribe to `<Model>` changes", "a computed list".
- "Regenerate SDL / `@angee/gql`", "write `documents.ts`", "schema `--check` fails".

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/schema.py` | Strawberry types, resources, authored roots, the `schemas` mapping | `src/angee/tags/schema.py`, `src/angee/money/schema.py`, `examples/src/example/notes/schema.py` |
| `<addon>/pyproject.toml` `[tool.angee.graphql] schema` | Only a non-default schema module reference; default is `schema.py` | `src/angee/graphql/schema.py` (`_raw_schemas`) |
| `src/angee/graphql/schema.py` | Buckets `SCHEMA_PART_KEYS`, merge/build per name and finalized metadata `GraphQLSchemas`, error sanitizer `AngeeSchema` | `SchemaParts`, `schema_parts_for` |
| `src/angee/graphql/data/hasura.py` | `hasura_model_resource`, `AngeeHasuraWriteBackend`, `HasuraLines`, `public_pk_decoder`, `declared_hasura_resource_fields` | `_check_writable_relations_decoded`, the writable-relation decode guard |
| `src/angee/graphql/data/pydantic_resource.py` | `hasura_pydantic_resource` (computed, read-only rows) | `_ROLE_RESOURCE`, `_GRANT_RESOURCE` in `src/angee/iam/schema.py` |
| `src/angee/graphql/actions.py` | `ActionResult`, `action_guard`, `authorized_action_target`, `authorized_permission_target`, `resolve_action_target` | `snooze_task` in `src/angee/work/schema.py` |
| `src/angee/graphql/{node,ids,deletion,capabilities}.py` | `AngeeNode` (`node.py`), `instance_for_id`/`require_instance_for_id` (`ids.py`), `delete_by_public_id`/`attach_delete_preview_metadata` (`deletion.py`), `permissions_field` (`capabilities.py`) | `NoteDeletePreviewMutation` in `examples/src/example/notes/schema.py` |
| `src/angee/graphql/subscriptions.py`, `apps.py` | `changes`; `GraphQLConfig.ready` (`apps.py`) registers the schema checks and connects publishers | `schemas` in `src/angee/knowledge/schema.py` |
| `src/angee/graphql/sdl.py`, `management/commands/schema.py`, `urls.py` | `GraphQLSdl` renders `runtime/schemas/<name>.graphql` + `<name>.metadata.json`; ready-phase `SchemaIntegration` emits through compose; `schema --check` reads; `graphql/<schema_name>/` (`urlpatterns` in `urls.py`) | `docs/composer.md#serving` |
| `packages/app/src/web-codegen.mjs`, `<addon>/web/src/documents*.ts` | Shared `generateWebRuntime` emits one `@angee/gql/<name>` per SDL; `documentGlobs` selects authored documents by file name; `buildOperationDocuments` derives action documents. Vite dev/build and the explicit CLI compose this owner | `src/angee/tags/web/src/documents.ts`, `useAuthoredQuery`, `useAuthoredMutation` in `packages/refine/src/dialect/authored-hooks.tsx` |

## Reuse first

- `AngeeNode` gives every type `id`, `display_name`, `redacted_fields`; `permissions_field(names)` answers held permissions. Never re-declare them.
- `hasura_model_resource` + `AngeeHasuraWriteBackend(model, public_id_fields=…)` + `public_pk_decoder` own list/detail/aggregate/groups/insert/update/delete roots and the resource metadata; a model never gets a hand-written CRUD root.
- Donors other addons already ship: `TaggedNode` + `tags_input_extensions` (`tags/schema.py`), `AuthoredRefMixin` (`iam/audit.py`), `integration_input_extensions` (`integrate/schema.py`).
- Verbs: `ActionResult` + `action_guard` + `authorized_action_target`/`authorized_permission_target`; cascade preview via `delete_by_public_id` + `attach_delete_preview_metadata`; sharing via `angee.graphql.sharing`.
- Live reads: `changes(Model, field=…)`; computed rows: `hasura_pydantic_resource`.
- Upstream first: `strawberry_django.type/field` (`only`, `select_related`, `prefetch_related`, `annotate`), `extend=True`, strawberry-django-hasura and -aggregates native knobs (`docs/stack.md#backend` rows).
- Web: `graphql()` from `@angee/gql/<schema>` with `useAuthoredQuery`/`useAuthoredMutation` (`@angee/refine`), `useRecordActionMutation` (`packages/ui/src/views/resource/record-action.ts`); resource views read metadata and need no documents.

## Steps

1. **Project the runtime model as a node type.** Resolve the concrete class through the app registry and declare only fields the API exposes.

   ```python
   Currency = apps.get_model("money", "Currency")

   @strawberry_django.type(Currency)
   class CurrencyType(AngeeNode):
       """Admin projection of one currency in the catalogue."""

       code: auto
       name: auto
       is_archived: auto
       created_at: auto

       @strawberry_django.field(only=["code", "name"])
       def display_name(self) -> str:
           return f"{self.code} — {self.name}"
   ```

   A relation projects as the target's node type (`currency: CurrencyType`); a computed label binds `AngeeNode.display_name` with the columns `__str__` reads (notes `NoteType.display_name`). If a closed set is hand-typed → an `enum.StrEnum` with `@strawberry.enum`, never `str`.
   **Owner rule:** the model owns fields, choices and relations; the type projects them from the composed runtime class, so enums and extensions already reflect every addon ([Rules](../../../docs/backend/guidelines.md#rules) **GraphQL types and enums bind to the composed runtime model**).

2. **Declare the resource once.** Query intent is declared here; capabilities are finalized from the composed schema.

   ```python
   _RATE_RESOURCE = hasura_model_resource(
       CurrencyRateType,
       model=CurrencyRate,
       name="currency_rates",
       filterable=["id", "currency", "date", "is_archived"],
       sortable=["date", "rate", "created_at"],
       aggregatable=["id"],
       groupable=["currency", "is_archived"],
       writable=["currency", "date", "rate"],
       field_id_decode={"currency": public_pk_decoder(Currency)},
       write_backend=AngeeHasuraWriteBackend(CurrencyRate, public_id_fields=("currency",)),
       id_column="sqid",
   )
   ```

   If insert and update differ → `insertable=`/`updatable=` (tags `_TAG_RESOURCE`). If a column is server-owned → leave it out of the write lists. If reads need a scope → `get_queryset=lambda info: Tag.objects.canonical()`. If a donor contributes columns → `declared_hasura_resource_fields(Model, "hasura_readable_fields")` (tags `_TAG_EXTENSION_READ_FIELDS`). If the rows are not a model → `hasura_pydantic_resource(RowModel, name=…, model_label=…, filterable=…, sortable=…, rows=…)` (iam `_ROLE_RESOURCE`).
   **Owner rule:** the resource is the one declaration of query and write capability; metadata, forms and MCP read the finalized result and never re-list fields ([Rules](../../../docs/backend/guidelines.md#rules) "Declare resource query intent once on the resource", [GraphQL actor and write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) **Server-owned fields are excluded from the write surface**).

3. **Author non-CRUD roots as thin dispatchers.** A resolver's budget is input, context, dispatch, result.

   ```python
   @strawberry.type
   class TagMutation:
       @strawberry.mutation(name="tag")
       def tag(self, target_type: str, target_id: PublicID, tag_ids: list[PublicID]) -> list[TagAssignmentType]:
           assignments = TagAssignment.objects.attach(target_type, str(target_id), [str(i) for i in tag_ids])
           return cast(list[TagAssignmentType], assignments)

   @strawberry.mutation
   @action_guard("Close cycle failed.")
   def close_work_cycle(self, info: strawberry.Info, cycle: PublicID) -> ActionResult:
       target = authorized_action_target(info, Cycle, cycle, "write")
       target.close()
       return ActionResult(ok=True, message="Cycle closed.", id=target.sqid)
   ```

   If the verb is row-authorized → `authorized_action_target` (write scope) or `authorized_permission_target(info, Model, id, "<perm>")` (exact permission). If the actor's role authorizes → `resolve_action_target` plus field `permission_classes`. If delete needs a cascade preview → `delete_by_public_id(Model, str(id), confirm=confirm, queryset=Model.objects.for_write())` wrapped by `attach_delete_preview_metadata` (notes `NoteDeletePreviewMutation.delete_note`).
   **Owner rule:** the manager/model verb owns state, locks and policy; the resolver may only translate and dispatch ([Put behavior on the owning object](../../../docs/guidelines.md#put-behavior-on-the-owning-object), [Rules](../../../docs/backend/guidelines.md#rules) "GraphQL resolvers stay thin").

4. **Extend another addon's type or input; never edit it.**

   ```python
   @strawberry_django.type(Page, name="PageType", extend=True)
   class PageTags(TaggedNode):
       """Console-only tags on a page; the public page node stays unchanged."""

   schemas = {"console": {..., "type_extensions": [PageTags],
                          "input_extensions": tags_input_extensions(_PAGE_RESOURCE)}}
   ```

   A hand-written input donor is `@strawberry.input(name="users_insert_input", extend=True)` (iam `UserPasswordInsertInput`); its model-side consumer composes `InputExtensionsMixin` (`base/models/inputs.py`), per the write-side contract in [Rules](../../../docs/backend/guidelines.md#rules) **Extension is symmetric across five axes**. Declare optimizer hints on the contributed field ([GraphQL pitfalls](references/pitfalls.md) **Type extensions retain native optimizer hints**).
   **Owner rule:** the field lands on the target wherever it appears; the bucket only gates registration, and a missing field type fails that bucket's build loudly ([Rules](../../../docs/backend/guidelines.md#rules) **Extension is symmetric across five axes**).

5. **Declare owned child rows as lines.**

   ```python
   _STAGE_LINES = HasuraLines(
       field="stages", model=Stage, node=WorkStageType,
       writable=("name", "category", "tone", "position", "rule_owned"),
       position_field="position", defaults={"category": "unstarted", "tone": "neutral"},
   )
   _QUEUE_RESOURCE = hasura_model_resource(WorkQueueType, model=Queue, ..., lines=_STAGE_LINES)
   ```

   If the child exists only as part of its parent and saves with it → lines; an associated record stays its own resource. A child whose state locks fields composes `RowLockMixin` and projects `locked_fields` on its node. A writable child relation needs `public_id_fields`.
   **Owner rule:** lines are the user's writes elevated by the parent's authorization; the child's own guards still run ([GraphQL actor and write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) **Lines are owned parts, written as the user**).

6. **Stream changes from the schema declaration only.**

   ```python
   "console": {**_NOTE_SCHEMA_BUCKET, "subscription": [changes(Note, field="noteChanged")]},
   ```

   If an event's read anchor is another row → the model implements `change_read_resource()`.
   **Owner rule:** `GraphQLConfig.ready` connects publishers from declared `changes` metadata; schema import and build never touch signals ([Schema lifecycle and change publication](../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication) **Publishers wire during `angee.graphql` app `ready()`**).

7. **Assemble `schemas` by named schema and bucket.**

   ```python
   _NOTE_SCHEMA_BUCKET = {
       "query": [_NOTE_RESOURCE.query],
       "mutation": [_NOTE_RESOURCE.mutation, NoteDeletePreviewMutation],
       "types": [NoteType, *_NOTE_RESOURCE.types],
   }
   schemas = {
       "public": {**_NOTE_SCHEMA_BUCKET},
       "console": {**_NOTE_SCHEMA_BUCKET, "subscription": [changes(Note, field="noteChanged")]},
   }
   ```

   Admin-only surfaces go to `console` alone (money, tags); outward reads also to `public`. Every resource's `.types` ride the `types` bucket. Values are sequences, never sets.
   **Owner rule:** the addon declares exposure per named schema; `GraphQLSchemas` merges buckets across addons, builds one Strawberry schema per name and finalizes resource metadata from it ([Rules](../../../docs/backend/guidelines.md#rules) "GraphQL authoring is native Strawberry").

8. **Emit SDL, run codegen, then ship documents only for authored operations.** Order: native `compose` (including SDL/metadata) → host codegen → fragment checks ([Composition and schema](../../../docs/checks.md#composition-and-schema)).

   ```ts
   import { graphql } from "@angee/gql/console";

   export const TagRecord = graphql(`
     mutation TagRecord($type: String!, $id: ID!, $tags: [ID!]!) {
       tag(target_type: $type, target_id: $id, tag_ids: $tags) { id }
     }
   `);
   ```

   `documents.ts`/`documents.<name>.ts` bind to a non-public schema, `documents.public.ts` to `public`; consume with `useAuthoredMutation(TagRecord, options)` (`RecordTagsWidget` in `tags/web/src/RecordTagsWidget.tsx`). If the mutation returns `ActionResult` with an eligible signature → no document; `useRecordActionMutation<ActionFieldName>("field")`. If the view is a resource list, detail or form → no document; metadata drives it.
   **Owner rule:** Python ships schema and operations, TypeScript ships UX; `@angee/*` packages stay schema-independent and only addon fragments import `@angee/gql` ([Frontend rules](../../../docs/frontend/guidelines.md#rules) "Python ships schema and operations. TypeScript ships UX." and **Schema dependence stops at the composition boundary**).

9. **Prove it with a non-admin reader.** A framework test builds the schema and executes as a user (`execute_schema` in `src/angee/graphql/testing/documents.py`, `test_hasura_prepared_factory_creates_one_companion` in `tests/test_graphql_insert.py`); an addon test drives the HTTP endpoint after `rebac sync` and resources (`IAMGraphQLTests` in `examples/src/example/notes/tests/test_iam_graphql.py`); a metadata snapshot pins the roots (`test_public_note_resource_metadata_snapshot` in `.../tests/test_schema_metadata.py`).
   **Owner rule:** admin-only tests pin neither deny behavior nor leaked `sudo()` scope ([GraphQL pitfalls](references/pitfalls.md) **GraphQL authorization tests include a non-admin reader**).



## Do not

- A hand-written list query or CRUD mutation for a model → `hasura_model_resource`; [Rules](../../../docs/backend/guidelines.md#rules) "GraphQL schema files declare Strawberry types".
- `from <addon>.models import Model` to bind a type → `apps.get_model`; [Rules](../../../docs/backend/guidelines.md#rules) "Cross-addon and generated-model references go through Django's app registry".
- Editing another addon's type or input, or importing its schema to add a field → `type_extensions` / `input_extensions`; [Rules](../../../docs/backend/guidelines.md#rules) **Extension is symmetric across five axes**.
- Permission, status or implementation branching in a resolver → model/manager verb + `authorized_*_target`; [Boundary leaks](../../../docs/guidelines.md#boundary-leaks-and-detail-driven-policy). Caveated subjects are rejected by `angee.E024` (`check_rebac_caveats` in `src/angee/base/checks.py`); the path is field-backed conditional relations.
- A writable FK or M2M without a decode → `public_id_fields` / `field_id_decode`; `_check_writable_relations_decoded` in `src/angee/graphql/data/hasura.py`.
- `readOnly` in a form to hide a server-owned column → exclude it from `insertable`/`updatable`; [GraphQL actor and write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) **Server-owned fields are excluded from the write surface**.
- `= None` on an optional create input over a non-null column → `strawberry.UNSET`; [GraphQL pitfalls](references/pitfalls.md) **`hasura_model_resource` create `full_clean`s the input**.
- Connecting publishers or signals at schema import → `apps.py` `ready()`; [Schema lifecycle and change publication](../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication) **Publishers wire during `angee.graphql` app `ready()`**.
- `info.selected_fields` to pick annotations → `sortable_aliases` / model `hasura_aliases`; [GraphQL actor and write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) **Resolvers never inspect `info.selected_fields` to choose annotations**.
- Hand-mirrored `…Data`/`…Variables` types, inline `graphql()` outside `documents*.ts`, or authored `ActionResult` documents → `@angee/gql/<schema>` + `useRecordActionMutation`; [Frontend rules](../../../docs/frontend/guidelines.md#rules) **Generated authored operations are typed, never hand-mirrored**.

## Verify

Validators: `angee.graphql.E001`/`E002` from Django system checks (every named schema builds, `src/angee/graphql/checks.py`), `angee.graphql.E003` (a serving check, `SERVING_TAG` in `src/angee/server/checks.py`: the in-memory channel layer refused when a Celery broker is configured; set `REDIS_URL` or the test opt-in `ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER`), `schema --check` for SDL and metadata drift, host codegen for documents, `tests/test_layering.py` for the contracts import closure, the focused pytest module, `ruff` and `mypy` on touched files. Commands and roots: [Python and composition contracts](../../../docs/checks.md#python-and-composition-contracts) (lane), [Composition and schema](../../../docs/checks.md#composition-and-schema) and [Frontend packages and addon fragments](../../../docs/checks.md#frontend-packages-and-addon-fragments) (stack root). Loop: `.agents/skills/slice/SKILL.md`.

Done means: `check` is clean; `runtime/schemas/<name>.graphql` shows the roots the resource declaration and authored fields imply, and `<name>.metadata.json` lists the resource with those roots as in the notes snapshot (`test_public_note_resource_metadata_snapshot`); `runtime/gql/<name>/` exports each authored document; the composed and per-package web typechecks pass; a non-admin reader test passes and a denied actor reads not-found.

A jj workspace lane has no host, database or JS install: run the focused pytest module (schema build + `execute_schema`), `ruff`, `mypy` and `test_layering.py` there; run `check`, `schema`, codegen and every web check only on integrated `src`. False greens: the composed `web` typecheck skips package test files, so the per-package typecheck is separate ([Frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments)); addon checks never regenerate documents, so a stale `@angee/gql` passes ([web build pitfalls](../angee-framework/references/web-pitfalls.md)); a failed `check` in one process does not stop a writer process from starting ([Schema lifecycle and change publication](../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication)).

## Pitfalls

- Create `full_clean`s the input: model and input defaults must agree, `JSONField(default=dict)` needs `blank=True`, and factory invariants belong on the queryset's `insert()` ([GraphQL pitfalls](references/pitfalls.md) **`hasura_model_resource` create `full_clean`s the input**).
- A status reads as the uppercase enum name and writes as the lowercase model value, inside line inputs too ([GraphQL actor and write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) **A status field is read/write-asymmetric**).
- `only=[...]` must list every column the resolver or an inherited `display_name` dereferences; test narrow selections at several row counts ([GraphQL pitfalls](references/pitfalls.md) **A `strawberry_django.field(only=[...])` hint must list every column the resolver dereferences**, [Rules](../../../docs/backend/guidelines.md#rules) "Declare computed GraphQL field dependencies with native Strawberry-Django").
- Intersect write-only fields out of the read selection or the detail query is invalid and the form loads blank ([GraphQL pitfalls](references/pitfalls.md) **Intersect write-only fields out of the read/return selection**).
- Input coercion errors fire before resolvers and never reach `validationErrors`; guard required inputs from `rootFields.requiredCreateFields` ([GraphQL actor and write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) **Validation surfaces two ways**).
- A custom value field registers its wire type beside its field module (`register_field_type`, `src/angee/graphql/field_types.py`), never from a later `ready()` ([Schema lifecycle and change publication](../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication) **A custom model value field registers its GraphQL wire type when its field module imports**).

## Read next

- [GraphQL pitfalls](references/pitfalls.md)
- `docs/backend/guidelines.md#rules`, `#graphql-actor-and-write-contracts`, `#schema-lifecycle-and-change-publication`, `#addon-websocket-endpoints`
- `docs/frontend/guidelines.md#rules`
- `docs/composer.md#serving`, `docs/checks.md#composition-and-schema`
- `docs/stack.md#backend` (strawberry-django, strawberry-django-aggregates, strawberry-django-hasura, channels), `#hasura-dialect-rule`
- `docs/glossary.md` (GraphQL data resource, Resource query)
- `angee-design`, `angee-models`, `angee-permissions`, `angee-pages`, `angee-forms`, `angee-tests`, `slice`

## Sources

<!--
Docs read in full: AGENTS.md; docs/guidelines.md; docs/backend/guidelines.md #composition-owners, #rules, #graphql-and-authorization, #addon-websocket-endpoints, #graphql-actor-and-write-contracts, #schema-lifecycle-and-change-publication; docs/stack.md #backend (strawberry-django, strawberry-django-aggregates, strawberry-django-hasura, channels, django-zed-rebac rows); docs/composer.md #serving; docs/checks.md #python-and-composition-contracts, #composition-and-schema, #frontend-packages-and-addon-fragments; docs/frontend/guidelines.md #package-layering, #rules, #framework-containers, #context-aside.
Code read: src/angee/graphql/schema.py and def list (AngeeSchema, SchemaParts, GraphQLSchemas, discovery); actions.py; sdl.py (whole); data/hasura.py and def list; data/pydantic_resource.py; subscriptions.py; node.py (whole); deletion.py; ids.py def list; capabilities.py; checks.py (whole); apps.py (whole); urls.py; management/commands/schema.py (whole); field_types.py; packages/app/src/web-codegen.mjs (`generateWebRuntime`, `documentGlobs`, `buildOperationDocuments`); packages/refine/src/dialect/authored-hooks.tsx; packages/ui/src/views/resource/record-action.ts; tests/conftest.py; tests/test_graphql_insert.py; tests/test_layering.py. No src/angee/graphql/README.md exists.
Reference addons (whole): src/angee/tags/schema.py, src/angee/money/schema.py, examples/src/example/notes/schema.py, src/angee/tags/web/src/documents.ts, RecordTagsWidget.tsx, index.tsx; examples/src/example/notes/tests/test_schema_metadata.py, test_iam_graphql.py. Extension and lines examples: src/angee/iam_integrate_oidc/schema.py; src/angee/knowledge/schema.py; src/angee/iam/schema.py; src/angee/work/schema.py; src/angee/base/models/inputs.py.
Mismatches: docs/frontend/guidelines.md#rules (**Generated authored operations are typed, never hand-mirrored**, "file name picks its schema") says documents.ts/documents.console.ts map to console; packages/app/src/web-codegen.mjs (`documentGlobs`) maps documents.ts plus documents.<name>.ts to every non-public schema name, so documents.ts also feeds any further named schema. No doc walks through declaring a resource end to end or the documents.ts consumer.
-->
