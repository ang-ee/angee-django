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
| `<addon>/schema.py` | Strawberry types, resources, authored roots, the `schemas` mapping | `addons/angee/tags/schema.py:44-72,205-261`, `addons/angee/money/schema.py:62-109`, `examples/addons/example/notes/schema.py:22-95` |
| `<addon>/addon.toml` `schemas` | Only a non-default schema module reference; default is `schema.py` | `addons/angee/graphql/schema.py:656` (`_raw_schemas`) |
| `addons/angee/graphql/schema.py` | Buckets `SCHEMA_PART_KEYS:67`, merge/build per name and finalized metadata `GraphQLSchemas:298`, error sanitizer `AngeeSchema:82` | `:229` `SchemaParts`, `:638` `schema_parts_for` |
| `addons/angee/graphql/data/hasura.py` | `hasura_model_resource:1374`, `AngeeHasuraWriteBackend:201`, `HasuraLines:107`, `public_pk_decoder:644`, `declared_hasura_resource_fields:1005` | `:1796` writable-relation decode guard |
| `addons/angee/graphql/data/pydantic_resource.py` | `hasura_pydantic_resource:50` (computed, read-only rows) | `addons/angee/iam/schema.py:692-709` |
| `addons/angee/graphql/actions.py` | `ActionResult:40`, `action_guard:195`, `authorized_action_target:240`, `authorized_permission_target:276`, `resolve_action_target:321` | `addons/angee/work/schema.py:480-495` |
| `addons/angee/graphql/{node,ids,deletion,capabilities}.py` | `AngeeNode` (`node.py:18`), `instance_for_id`/`require_instance_for_id` (`ids.py:54,68`), `delete_by_public_id`/`attach_delete_preview_metadata` (`deletion.py:334,396`), `permissions_field` (`capabilities.py:135`) | `examples/addons/example/notes/schema.py:41-62` |
| `addons/angee/graphql/subscriptions.py`, `apps.py` | `changes:30`; `GraphQLConfig.ready` (`apps.py:22`) registers the schema checks and connects publishers | `addons/angee/knowledge/schema.py:845-852` |
| `addons/angee/graphql/sdl.py`, `management/commands/schema.py`, `urls.py` | `GraphQLSdl:27` renders `runtime/schemas/<name>.graphql` + `<name>.metadata.json`; `schema [--check]`; `graphql/<schema_name>/` (`urls.py:11`) | `docs/composer.md#serving` |
| `packages/app/bin/angee-web-codegen.mjs`, `<addon>/web/src/documents*.ts` | One `@angee/gql/<name>` per SDL (`:67-70`), document globs by file name (`:467-472`), derived action documents (`:475`) | `addons/angee/tags/web/src/documents.ts`, `packages/refine/src/dialect/authored-hooks.tsx:92,213` |

## Reuse first

- `AngeeNode` gives every type `id`, `display_name`, `redacted_fields`; `permissions_field(names)` answers held permissions. Never re-declare them.
- `hasura_model_resource` + `AngeeHasuraWriteBackend(model, public_id_fields=…)` + `public_pk_decoder` own list/detail/aggregate/groups/insert/update/delete roots and the resource metadata; a model never gets a hand-written CRUD root.
- Donors other addons already ship: `TaggedNode` + `tags_input_extensions` (`tags/schema.py:116,147`), `AuthoredRefMixin` (`iam/audit.py:39`), `integration_input_extensions` (`integrate/schema.py:1631`).
- Verbs: `ActionResult` + `action_guard` + `authorized_action_target`/`authorized_permission_target`; cascade preview via `delete_by_public_id` + `attach_delete_preview_metadata`; sharing via `angee.graphql.sharing`.
- Live reads: `changes(Model, field=…)`; computed rows: `hasura_pydantic_resource`.
- Upstream first: `strawberry_django.type/field` (`only`, `select_related`, `prefetch_related`, `annotate`), `extend=True`, strawberry-django-hasura and -aggregates native knobs (`docs/stack.md#backend` rows at `:55-60`).
- Web: `graphql()` from `@angee/gql/<schema>` with `useAuthoredQuery`/`useAuthoredMutation` (`@angee/refine`), `useRecordActionMutation` (`packages/ui/src/views/resource/record-action.ts:93`); resource views read metadata and need no documents.

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

   A relation projects as the target's node type (`currency: CurrencyType`); a computed label binds `AngeeNode.display_name` with the columns `__str__` reads (notes `:26-28`). If a closed set is hand-typed → an `enum.StrEnum` with `@strawberry.enum`, never `str`.
   **Owner rule:** the model owns fields, choices and relations; the type projects them from the composed runtime class, so enums and extensions already reflect every addon ([Rules](../../../docs/backend/guidelines.md#rules) `:441`).

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

   If insert and update differ → `insertable=`/`updatable=` (tags `:214-215`). If a column is server-owned → leave it out of the write lists. If reads need a scope → `get_queryset=lambda info: Tag.objects.canonical()`. If a donor contributes columns → `declared_hasura_resource_fields(Model, "hasura_readable_fields")` (tags `:178-184`). If the rows are not a model → `hasura_pydantic_resource(RowModel, name=…, model_label=…, filterable=…, sortable=…, rows=…)` (iam `:692-699`).
   **Owner rule:** the resource is the one declaration of query and write capability; metadata, forms and MCP read the finalized result and never re-list fields ([Rules](../../../docs/backend/guidelines.md#rules) `:231`, [Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1436`).

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

   If the verb is row-authorized → `authorized_action_target` (write scope) or `authorized_permission_target(info, Model, id, "<perm>")` (exact permission). If the actor's role authorizes → `resolve_action_target` plus field `permission_classes`. If delete needs a cascade preview → `delete_by_public_id(Model, str(id), confirm=confirm, queryset=Model.objects.for_write())` wrapped by `attach_delete_preview_metadata` (notes `:41-62`).
   **Owner rule:** the manager/model verb owns state, locks and policy; the resolver may only translate and dispatch ([Put behavior on the owning object](../../../docs/guidelines.md#put-behavior-on-the-owning-object), [Rules](../../../docs/backend/guidelines.md#rules) `:198`).

4. **Extend another addon's type or input; never edit it.**

   ```python
   @strawberry_django.type(Page, name="PageType", extend=True)
   class PageTags(TaggedNode):
       """Console-only tags on a page; the public page node stays unchanged."""

   schemas = {"console": {..., "type_extensions": [PageTags],
                          "input_extensions": tags_input_extensions(_PAGE_RESOURCE)}}
   ```

   A hand-written input donor is `@strawberry.input(name="users_insert_input", extend=True)` (iam `:747`); its model-side consumer composes `InputExtensionsMixin` (`base/models/inputs.py:9`), per the write-side contract in [Rules](../../../docs/backend/guidelines.md#rules) `:449`. Declare optimizer hints on the contributed field ([Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1393`).
   **Owner rule:** the field lands on the target wherever it appears; the bucket only gates registration, and a missing field type fails that bucket's build loudly ([Rules](../../../docs/backend/guidelines.md#rules) `:449`).

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
   **Owner rule:** lines are the user's writes elevated by the parent's authorization; the child's own guards still run ([Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1421`).

6. **Stream changes from the schema declaration only.**

   ```python
   "console": {**_NOTE_SCHEMA_BUCKET, "subscription": [changes(Note, field="noteChanged")]},
   ```

   If an event's read anchor is another row → the model implements `change_read_resource()`.
   **Owner rule:** `GraphQLConfig.ready` connects publishers from declared `changes` metadata; schema import and build never touch signals ([Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1459`).

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
   **Owner rule:** the addon declares exposure per named schema; `GraphQLSchemas` merges buckets across addons, builds one Strawberry schema per name and finalizes resource metadata from it ([Rules](../../../docs/backend/guidelines.md#rules) `:436`).

8. **Emit SDL, run codegen, then ship documents only for authored operations.** Order: `schema` → host codegen → fragment checks ([Composition and schema](../../../docs/checks.md#composition-and-schema)).

   ```ts
   import { graphql } from "@angee/gql/console";

   export const TagRecord = graphql(`
     mutation TagRecord($type: String!, $id: ID!, $tags: [ID!]!) {
       tag(target_type: $type, target_id: $id, tag_ids: $tags) { id }
     }
   `);
   ```

   `documents.ts`/`documents.<name>.ts` bind to a non-public schema, `documents.public.ts` to `public`; consume with `useAuthoredMutation(TagRecord, options)` (`tags/web/src/RecordTagsWidget.tsx:23`). If the mutation returns `ActionResult` with an eligible signature → no document; `useRecordActionMutation<ActionFieldName>("field")`. If the view is a resource list, detail or form → no document; metadata drives it.
   **Owner rule:** Python ships schema and operations, TypeScript ships UX; `@angee/*` packages stay schema-independent and only addon fragments import `@angee/gql` ([Frontend rules](../../../docs/frontend/guidelines.md#rules) `:189-215`).

9. **Prove it with a non-admin reader.** A framework test builds the schema and executes as a user (`tests/conftest.py:893` `execute_schema`, `tests/test_graphql_insert.py:100-127`); an addon test drives the HTTP endpoint after `rebac sync` and resources (`examples/addons/example/notes/tests/test_iam_graphql.py:19-43`); a metadata snapshot pins the roots (`.../tests/test_schema_metadata.py:15-60`).
   **Owner rule:** admin-only tests pin neither deny behavior nor leaked `sudo()` scope ([Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1364`).

<details><summary>Changes under the build/host plan</summary>

Under the planned build/host split, SDL and
metadata are emitted by `provision` in a plain Django process after migrate, `rebac sync`
and resources; boot-time SDL writes and `emit_if_stale` are deleted. Step 8 then depends
on provision, not on a served process. `angee/hooks.py` (the `ANGEE_GRAPHQL_RESOURCE_FILTERS`
check in `graphql/apps.py:13`) follows the host into `angee.base`; re-cite on move.

</details>

## Do not

- A hand-written list query or CRUD mutation for a model → `hasura_model_resource`; [Rules](../../../docs/backend/guidelines.md#rules) `:203`.
- `from <addon>.models import Model` to bind a type → `apps.get_model`; [Rules](../../../docs/backend/guidelines.md#rules) `:441`.
- Editing another addon's type or input, or importing its schema to add a field → `type_extensions` / `input_extensions`; [Rules](../../../docs/backend/guidelines.md#rules) `:449`.
- Permission, status or implementation branching in a resolver → model/manager verb + `authorized_*_target`; [Boundary leaks](../../../docs/guidelines.md#boundary-leaks-and-detail-driven-policy). Caveated subjects are rejected by `angee.E024` (`addons/angee/base/checks.py:37`); the path is field-backed conditional relations.
- A writable FK or M2M without a decode → `public_id_fields` / `field_id_decode`; `addons/angee/graphql/data/hasura.py:1796`.
- `readOnly` in a form to hide a server-owned column → exclude it from `insertable`/`updatable`; [Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1436`.
- `= None` on an optional create input over a non-null column → `strawberry.UNSET`; [Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1366`.
- Connecting publishers or signals at schema import → `apps.py` `ready()`; [Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1459`.
- `info.selected_fields` to pick annotations → `sortable_aliases` / model `hasura_aliases`; [Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1535`.
- Hand-mirrored `…Data`/`…Variables` types, inline `graphql()` outside `documents*.ts`, or authored `ActionResult` documents → `@angee/gql/<schema>` + `useRecordActionMutation`; [Frontend rules](../../../docs/frontend/guidelines.md#rules) `:197-217`.

## Verify

Validators: `angee.graphql.E001`/`E002` from Django system checks (every named schema builds, `addons/angee/graphql/checks.py`), `schema --check` for SDL and metadata drift, host codegen for documents, `tests/test_layering.py` for the contracts import closure, the focused pytest module, `ruff` and `mypy` on touched files. Commands and roots: [Python and composition contracts](../../../docs/checks.md#python-and-composition-contracts) (lane), [Composition and schema](../../../docs/checks.md#composition-and-schema) and [Frontend packages and addon fragments](../../../docs/checks.md#frontend-packages-and-addon-fragments) (stack root). Loop: `.agents/skills/slice/SKILL.md`.

Done means: `check` is clean; `runtime/schemas/<name>.graphql` shows the roots the resource declaration and authored fields imply, and `<name>.metadata.json` lists the resource with those roots as in the notes snapshot (`test_schema_metadata.py:15-60`); `runtime/gql/<name>/` exports each authored document; the composed and per-package web typechecks pass; a non-admin reader test passes and a denied actor reads not-found.

A jj workspace lane has no host, database or JS install: run the focused pytest module (schema build + `execute_schema`), `ruff`, `mypy` and `test_layering.py` there; run `check`, `schema`, codegen and every web check only on integrated `src`. False greens: the composed `web` typecheck skips package test files, so the per-package typecheck is separate ([Frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments)); addon checks never regenerate documents, so a stale `@angee/gql` passes ([Frontend pitfalls](../../../docs/frontend/guidelines.md#pitfalls) `:1571`); a failed `check` in one process does not stop a writer process from starting ([Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1465`).

## Pitfalls

- Create `full_clean`s the input: model and input defaults must agree, `JSONField(default=dict)` needs `blank=True`, and factory invariants belong on the queryset's `insert()` ([Pitfalls](../../../docs/backend/guidelines.md#graphql-and-authorization) `:1366`).
- A status reads as the uppercase enum name and writes as the lowercase model value, inside line inputs too (`:1408`).
- `only=[...]` must list every column the resolver or an inherited `display_name` dereferences; test narrow selections at several row counts (`:1390`, [Rules](../../../docs/backend/guidelines.md#rules) `:208`).
- Intersect write-only fields out of the read selection or the detail query is invalid and the form loads blank (`:1433`).
- Input coercion errors fire before resolvers and never reach `validationErrors`; guard required inputs from `rootFields.requiredCreateFields` (`:1444`).
- A custom value field registers its wire type beside its field module (`register_field_type`, `addons/angee/graphql/field_types.py:33`), never from a later `ready()` (`:1502`).

## Read next

- `docs/backend/guidelines.md#rules` (`:198-236`, `:436-487`), `#graphql-and-authorization`, `#graphql-actor-and-write-contracts`, `#addon-websocket-endpoints`
- `docs/frontend/guidelines.md#rules` (`:189-230`), `#pitfalls` (`:1571-1581`)
- `docs/composer.md#serving`, `docs/checks.md#composition-and-schema`
- `docs/stack.md#backend` (strawberry-django, strawberry-django-aggregates, strawberry-django-hasura, channels), `#hasura-dialect-rule`
- `docs/glossary.md` (GraphQL data resource, Resource query)
- `angee-design`, `angee-models`, `angee-permissions`, `angee-pages`, `angee-forms`, `angee-tests`, `slice`

## Sources

<!--
Docs read in full: AGENTS.md; docs/guidelines.md; docs/backend/guidelines.md:116-139 (Composition owners), :140-489 (Rules), :1362-1543 (GraphQL and authorization), :2056-2097 (Addon WebSocket endpoints, GraphQL actor and write contracts); docs/stack.md:45-80 (rows 55 strawberry-django, 59 aggregates, 60 hasura, 75 channels, 76 django-zed-rebac); docs/composer.md:534-557 (Serving); docs/checks.md:30-112, :168-248; docs/frontend/guidelines.md:22-127, :185-235, :1565-1585, :1700-1725.
Code read: addons/angee/graphql/schema.py:1-80 and def list (:82 AngeeSchema, :229 SchemaParts, :298 GraphQLSchemas, :638-690 discovery); actions.py:1-80, :195-380; sdl.py (whole); data/hasura.py:1-160, :1374-1500, :1786-1842 and def list; data/pydantic_resource.py:1-120; subscriptions.py:1-82; node.py (whole); deletion.py:334-405; ids.py def list; capabilities.py:135-150; checks.py (whole); apps.py (whole); urls.py:11; management/commands/schema.py (whole); field_types.py:33; packages/app/bin/angee-web-codegen.mjs:55-260, :435-480; packages/refine/src/dialect/authored-hooks.tsx:92,213; packages/ui/src/views/resource/record-action.ts:93; tests/conftest.py:893-912; tests/test_graphql_insert.py:1-127; tests/test_layering.py:95-135. No addons/angee/graphql/README.md exists.
Reference addons (whole): addons/angee/tags/schema.py, addons/angee/money/schema.py, examples/addons/example/notes/schema.py, addons/angee/tags/web/src/documents.ts, RecordTagsWidget.tsx:1-40, index.tsx:46; examples/addons/example/notes/tests/test_schema_metadata.py:1-60, test_iam_graphql.py:1-50. Extension and lines examples: addons/angee/iam_integrate_oidc/schema.py:250-300; addons/angee/knowledge/schema.py:800-855; addons/angee/iam/schema.py:692-720, :747; addons/angee/work/schema.py:480-560; addons/angee/base/models/inputs.py:9-15.
Mismatches: frontend/guidelines.md:205 says documents.ts/documents.console.ts map to console; angee-web-codegen.mjs:467-472 maps documents.ts plus documents.<name>.ts to every non-public schema name, so documents.ts also feeds any further named schema. No doc walks through declaring a resource end to end or the documents.ts consumer.
-->
