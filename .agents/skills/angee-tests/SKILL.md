---
name: angee-tests
description: "Authors an Angee addon's tests: bare-host test models, composed_tables/composed_permissions, per-actor permission tests, verb and queryset tests, resource loads, schema-driven GraphQL tests, vitest fragment tests, story fixtures and e2e specs. Use for 'write tests for <addon>', 'add e2e'."
---

# Angee Tests

This skill authors tests; running them, their roots and CI coverage belong to `docs/checks.md`.

## Use when

- "Write / add tests for the `<addon>` addon", "cover `<Model>` for owner, grantee and outsider".
- "Add a GraphQL test for the `<resource>` root or the `<verb>` mutation", "prove the query budget".
- "Test the `<addon>` web fragment / `<Page>` declarations / the `<widget>`", "add a story fixture".
- "Add an e2e spec or Page Object for `/<route>`".
- Not for selecting or running checks: `docs/checks.md` owns commands and roots.

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/testing/models.py`, `tests/settings.py`, `tests/conftest.py` | concrete models of the bare host, the app roster, one-time registration | `addons/angee/tags/testing/models.py:15-63`, `tests/settings.py:29-110`, `tests/conftest.py:645` |
| `addons/angee/base/testing/fixtures.py`, `permissions.py` | `django_db_setup` (`:17`), `composed_tables` (`:58`); `composed_permissions` (`permissions.py:41`) | module docstrings |
| `tests/conftest.py` | actors and schema drivers: `create_user` (`:789`), `create_platform_admin` (`:974`), `vault_for` (`:795`), `addon_schema` (`:871`), `execute_schema` (`:893`), `result_data` (`:913`) | `tests/test_tags.py:46-55` |
| `tests/test_<addon>*.py` (framework) / `<addon>/tests/test_*.py` (consumer) | the test modules, one per concern | `tests/test_tags.py`, `examples/addons/example/notes/tests/` |
| `tests/composed_host.py` | the composed-host subprocess that runs consumer `TransactionTestCase` modules in the framework suite | `tests/test_composed_note_workflow.py:17-40` |
| `packages/app/src/testing.tsx`, `packages/ui/src/testing.tsx` | `expectValidBaseAddon` (`:101`), `chromeSnapshotForRoute`, `TEST_SCHEMAS` (`:354`); `createUiTestProviders` (`ui:35`) | `addons/angee/tags/web/src/index.test.ts` |
| `packages/app/config/vitest.ts`, `addons/angee/vitest.shared.ts` | `defineAngeeWebVitestConfig` (`:90`), `gqlAliasFor` (`:30`); the addon-tree `gqlAlias` | `addons/angee/tags/web/vitest.config.ts` |
| `packages/storybook/src/stories/runtime-fixtures.tsx`, `.storybook/main.mjs`, `scripts/catalog.test.mjs` | `storySchema` (`:111`), `jsonResponse` (`:100`), `RuntimeFixture` (`:144`); addon story discovery; token-drift scan over `addons/` | `addons/angee/workflows/web/src/TriggersPage.stories.tsx` |
| `packages/e2e/src/` | `test`/`api` (`fixtures.ts:25`), `defineE2EConfig` (`config.ts:27`), `loginViaApi` (`auth.ts:23`), `roleStatePath` (`auth.ts:41`), `PageObject` (`pom.ts:9`) | `examples/e2e/` (`@angee-example/notes-e2e`) |

## Reuse first

- Database and permission lifecycle: `composed_tables` / `composed_permissions` (`angee.base.testing`), native `db` when neither transactions nor REBAC matter; never a private `rebac sync` fixture.
- Actors: `create_user`, `create_platform_admin`, `rebac.actor_context` / `system_context`, `rebac.roles.grant`, `write_relationships`; a grantee through the model's `grant_record_access`.
- Stand-in records from owning test apps: `angee.messaging.testing.models` (`Party`), `angee.projects.testing.models` (`Task`), `tests.conftest` (`Page`, `File`, `vault_for`); `tests/mtidemo`, `hierdemo`, `recordrefdemo` for structural probes.
- Schema execution: `addon_schema` + `execute_schema` + `result_data` over one addon's `schemas`; Django `Client` POSTs to `/graphql/public/` or `/graphql/console/` on a composed host.
- Workflow and decision states: `angee.workflows.testing.drivers` (`load_workflow`, `start_run`, `run_until`, `decide`, `capture_tasks`, `observe`), never fabricated step runs ([source-addon test models](../../../docs/checks.md#source-addon-test-models)).
- Query budgets: Django `CaptureQueriesContext` at 1 and 25 rows (`tests/test_tags_field.py:118-143`).
- Frontend: `@angee/app/testing`, `@angee/ui/testing`, `@angee/refine/testing`, `@angee/metadata/testing` (`testDataResource`), `@angee/storybook/testing`, Testing Library, `vi.mock` for one container read.
- Browser: `@angee/e2e` only (`test`, `expect`, `api`, `requireGraphQLData`, `grantRecordAccess`), Page Objects under `pages/`.

## Steps

1. **Map the set before writing.** A complete addon carries, backend: permissions per actor, verbs and querysets, resource load, GraphQL roots and mutations, schema metadata, query budget, and one module per shipped seam (steps, commands, MCP tools): `examples/addons/example/notes/tests/` has nine such modules; `tests/test_tags*.py` splits the vocabulary, the placeable field and the MTI edge. Web: `index.test.ts`, a page-declaration test, `.native.test.tsx` provider tests, widget tests, stories; browser: a Page Object and specs. Name modules `test_<concern>.py` and tests `test_<behavior>`. **Owner rule:** one concern per module so focused selection and discovery agree ([source-addon test models](../../../docs/checks.md#source-addon-test-models)).

2. **Register concrete models (framework addon only).** The bare host runs no composer, so each abstract source model needs one concrete subclass, listed in `INSTALLED_APPS` and imported once from the root conftest before database setup.

   ```python
   # addons/angee/tags/testing/models.py
   class TagAssignment(AbstractTagAssignment):
       class Meta(AbstractTagAssignment.Meta):
           abstract = False
           app_label = "tags"
           db_table = "test_tags_assignment"
           rebac_resource_type = "tags/tag_assignment"

   class TagRole(AbstractTagRole):          # const-backed role anchor: a type, never a table
       class Meta(AbstractTagRole.Meta):
           abstract = False
           managed = False
           app_label = "tags"
           rebac_resource_type = "tags/role"
   ```

   Then `"angee.tags"` in `tests/settings.py` `INSTALLED_APPS` (`:29-110`) and `from angee.tags.testing import models as tags_models  # noqa: F401` in `tests/conftest.py` after its FK targets (`:645`). If the addon is a consumer → no test models: its tests run on the composed host (step 3). **Owner rule:** Django owns static test-table lifecycle; one concrete model per resource type ([environment and checks](references/environment.md)).

3. **Pick the database contract per module.** Framework modules take a fixture: `composed_tables` for transactional isolation plus REBAC sync, `composed_permissions` when the module needs other addons' `permissions.extends.zed` fragments merged (tags, MTI, placeable fields), native `db` for pure model tests.

   ```python
   pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_permissions")]
   ```

   Consumer modules are `TransactionTestCase` classes that `call_command("rebac", "sync")` and `call_command("resources", "load", include_demo=True, allow_non_dev=True)` in `setUp` and read `apps.get_model("notes", "Note")` (`examples/addons/example/notes/tests/test_rebac.py:23-33`); the framework suite drives them through `tests/composed_host.py --action tests --test-label example.notes.tests.<module>`, consumer CI through `consumer-tests`. Mark PostgreSQL-only modules `pytest.mark.skipif(connection.vendor != "postgresql", …)` (`tests/test_messaging_delivery_concurrency.py:8`). **Owner rule:** pytest-django and the base plugin own setup and flush; `--nomigrations` and PostgreSQL settings belong to the CI selection, never to a module ([Python contracts](../../../docs/checks.md#python-and-composition-contracts)).

4. **Prove permissions per actor.** Seed one fixture with the owner, an outsider and the record under `actor_context`, then assert read, write and deny for each actor, plus the backing (field-backed and const-backed relations store no tuples).

   ```python
   @pytest.fixture()
   def party_edge(composed_permissions: None) -> SimpleNamespace:
       owner, outsider = create_user("tags-party-owner"), create_user("tags-outsider")
       with system_context(reason="tags test setup"):
           tag = Tag.objects.create(name="VIP")
       with actor_context(owner):
           party = Party.objects.create(display_name="Delta Co")
       party_address = ("parties/party", public_id_for(Party, party.pk))
       return SimpleNamespace(owner=owner, outsider=outsider, tag=tag, party=party, party_address=party_address)

   def test_an_outsider_sees_none_of_the_records_edges(party_edge):
       with actor_context(party_edge.outsider):
           assert not TagAssignment.objects.for_record(party_edge.party).exists()
   ```

   Include a non-admin reader and a platform admin (`create_platform_admin`, or `Note.objects.as_user(admin)` reaching every row through the const-backed role, `test_rebac.py:112-140`). **Owner rule:** `permissions.zed` owns the rule; the test proves outcomes per actor, never the zed text ([GraphQL pitfalls](../angee-graphql/references/pitfalls.md), [authorization pitfalls](../angee-permissions/references/pitfalls.md)).

5. **Test verbs and querysets on their owner.** Call the manager verb, not a raw `create`, and assert idempotence, fail-fast denial and the constraint.

   ```python
   def test_attach_creates_the_edge_idempotently_under_the_actor(party_edge):
       with actor_context(party_edge.owner):
           first = TagAssignment.objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
           again = TagAssignment.objects.attach(*party_edge.party_address, [party_edge.tag.sqid])
       assert [row.pk for row in again] == [row.pk for row in first]

   def test_the_same_tag_attaches_once_per_target(party_edge):
       edge = dict(tag=party_edge.tag, content_type=ContentType.objects.get_for_model(Party), object_id=party_edge.party.pk)
       with system_context(reason="tags test setup"):
           TagAssignment.objects.create(**edge)
           with pytest.raises(IntegrityError), transaction.atomic():
               TagAssignment.objects.create(**edge)
   ```

   Aggregates: assert `scoped_for_aggregate()` is actor-scoped, unscoped only under `sudo()`/`system_context`, and empty with no actor (`test_rebac.py:64-82`). **Owner rule:** the queryset or manager owns the verb, the test exercises it through that contract ([Persistence primitives](../../../docs/backend/guidelines.md#persistence-primitives)).

6. **Load declared resources.** Framework: write a throwaway addon and load one tier; consumer: load the demo tier and prove idempotence.

   ```python
   addon = make_addon(name="tests.chatter_seed", label="chatter_seed", path=tmp_path,
                      resources={"install": list(files)})            # tests/test_messaging_resources.py:28
   result = Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
   assert result.created == 5

   call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
   with system_context(reason="test"):
       assert Note.objects.count() == notes_before                    # test_rebac.py:142-149
   ```

   The repository-wide value sweep (`tests/test_resource_fixtures.py`) already validates every declared value against its field; do not repeat it. **Owner rule:** the loader owns xref and tier semantics; a test asserts rows and grants, not file parsing ([Resource loading](../../../docs/backend/guidelines.md#resource-loading)).

7. **Drive GraphQL through the schema.** Bare host: build the addon's own schema and execute as an actor; composed host: POST as a logged-in `Client`.

   ```python
   schema = addon_schema(tags_schema.schemas, "console")
   assert execute_schema(schema, _TAG, _verb_variables(edge), user=curator).errors   # denied
   data = result_data(execute_schema(schema, query, {"limit": size}, user=drive.alice))

   # composed host (examples/addons/example/notes/tests/test_iam_graphql.py:505-539)
   self.login(client, "alice")                       # the login mutation, not force_login
   payload = self.post(Client(), 'mutation { insert_notes_one(object: {title: "x"}) { id } }')
   self.assertEqual(payload["errors"][0]["extensions"]["code"], "PERMISSION_DENIED")
   ```

   Cover: a non-admin reader, the denied anonymous write with its code, `_by_pk` scoped out for the wrong actor, aggregates and groups equal to the actor-scoped ORM count, and an identical query count at 1 and 25 rows. Snapshot resource metadata through `GraphQLSdl.from_discovery().render_metadata()` (`test_schema_metadata.py`). **Owner rule:** resolvers are thin callers; test the schema contract, never a resolver function ([GraphQL pitfalls](../angee-graphql/references/pitfalls.md), [authorization pitfalls](../angee-permissions/references/pitfalls.md)).

8. **Test the web fragment.** `web/vitest.config.ts` composes the published builder with the addon-tree alias; consumers pass `gqlAlias: []` until they import `@angee/gql`.

   ```ts
   // addons/angee/tags/web/vitest.config.ts
   import { defineAngeeWebVitestConfig } from "@angee/app/vitest";
   import { gqlAlias } from "../../vitest.shared";
   export default defineAngeeWebVitestConfig({ gqlAlias });

   // src/index.test.ts — the manifest invariants, routes, menus, containers
   expect(() => expectValidBaseAddon(tags)).not.toThrow();
   expect(Object.keys(tags.containers ?? {})).toEqual(["tags.tags#facets", "tags.tags#columns", "tags.tags#fields"]);
   ```

   Page declarations: call the page as a function and parse `pageChildren`/`parsePageColumns`, mocking `useContainer` per address (`TagsPage.test.tsx:22-30`). Providers: `.native.test.tsx` under `// @vitest-environment happy-dom` with `createUiTestProviders({ resources: [testDataResource(...)] })` and a `dataProvider` of `vi.fn`s (`RecordTagsWidget.native.test.tsx:15-31`). Widgets: render `widget.read`/`widget.edit` and assert currency-driven facts (`addons/angee/money/web/src/widgets/money.test.tsx`). Stories: export the fixture component from `*.stories.tsx` (`excludeStories`), feed it `storySchema(fetch)` + `jsonResponse`, and reuse it from the native test (`TriggersPage.native.test.tsx:4-11`); Storybook discovers `*/*/web/src/**/*.stories.tsx` from the stack root. **Owner rule:** `@angee/app` owns the Vitest defaults and the manifest contract; a fragment adds only its alias and its own assertions ([frontend packages and addon fragments](../../../docs/checks.md#frontend-packages-and-addon-fragments), [story fixtures](references/web-pitfalls.md)).

9. **Add browser specs.** A consumer e2e package is `package.json` (`@angee/e2e` as `workspace:*`, `test:e2e: playwright test`), a one-line config, `tests/auth.setup.ts`, `pages/<route>-page.ts`, `tests/<concern>.spec.ts`.

   ```ts
   export default defineE2EConfig();                       // playwright.config.ts

   export class NotesPage extends PageObject {             // pages/notes-page.ts
     readonly path = "/notes";
     get newNoteButton(): Locator { return this.page.getByRole("button", { name: "New note" }); }
   }

   test.describe("alice — authenticated", () => {         // tests/notes.spec.ts
     test.use({ storageState: roleStatePath("alice") });
     test("sees her scoped notes", async ({ page, api }) => {
       await new NotesPage(page).goto();                 // inherited from PageObject
       expect(requireGraphQLData(await api.query<NotesData>(NOTES_QUERY)).notes_aggregate.aggregate.count).toBeGreaterThan(0);
     });
   });
   ```

   Mutating specs own their rows through a `test.extend` fixture that creates and deletes (`examples/e2e/tests/fixtures/notes.ts:14-33`); assert invariants (a known title, disjoint scopes, `PERMISSION_DENIED`), never seed counts. Shared-stack overrides (`workers: 1`) stay in the consumer config (`examples/e2e/playwright.config.ts`). **Owner rule:** the stack owns data, `@angee/e2e` owns login and transport, the Page Object owns selectors ([Page Object Model](../../../docs/frontend/e2e.md#authoring-style-page-object-model)).

## Do not

- Write a private REBAC-sync or table fixture beside `composed_tables`/`composed_permissions` ([source-addon test models](../../../docs/checks.md#source-addon-test-models)).
- Register a second concrete model for a resource type, or create tables by hand instead of `tests/tables.py` `model_tables` ([environment and checks](references/environment.md)).
- Ship admin-only GraphQL tests, or `force_login` without `backend="angee.iam.auth.ModelBackend"` ([GraphQL pitfalls](../angee-graphql/references/pitfalls.md), [authorization pitfalls](../angee-permissions/references/pitfalls.md)).
- Add a caveated subject to make a conditional grant testable; use a field-backed relation (`angee.E024`, `addons/angee/base/checks.py:37`).
- Put `--nomigrations` or PostgreSQL settings into a module; mark PostgreSQL-only modules and let them skip on SQLite ([Python contracts](../../../docs/checks.md#python-and-composition-contracts)).
- Pass a per-test timeout for a slow render, or copy provider setup instead of `createUiTestProviders`/`createRefineTestProviders` ([Frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments)).
- Import `test`/`expect` from `@playwright/test` in a spec or Page Object ([what ships](../../../docs/frontend/e2e.md#what-ships-in-the-framework)).
- Assert seed row counts or add harness serialization to hide a locking failure ([isolation depth](../../../docs/frontend/e2e.md#isolation-depth)).
- Assert that prescribed words occur in a file instead of an outcome at a boundary ([proven practices](../../../docs/guidelines.md#follow-proven-best-practices-and-patterns)).
- Weaken or skip a failing test to make a check green ([reconcile](../../../docs/guidelines.md#reconcile-code-docs-and-tests)).

## Verify

Validators: the new modules run standalone, the owning guards (`tests/test_layering.py`, `tests/test_permission_composition.py`, `packages/app/src/architecture-guardrails.test.ts`) stay green, and for UI the live page is exercised; commands and roots in [Python contracts](../../../docs/checks.md#python-and-composition-contracts), [Frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments), [Browser verification](../../../docs/checks.md#browser-verification); loop in `.agents/skills/slice/SKILL.md`.

- In a jj lane (no host, database or JS install): each changed Python module alone, then with the addon's other modules; ruff and mypy on touched files; the layering tests. Consumer `TransactionTestCase` modules need a composed host and do not run here.
- Only on integrated `src`: the composed-host runs (`tests/test_composed_*.py`, consumer modules), per-package `typecheck` (it includes `*.test.tsx`), vitest per fragment, `catalog:check`, the browser suite against a seeded stack, and after a schema change codegen first ([Composition](../../../docs/checks.md#composition-and-schema)).
- Done means: every actor row in step 4 is asserted (owner, grantee, outsider, non-admin reader, admin), every verb has its deny path, GraphQL covers the denied anonymous write and the 1/25-row budget, `expectValidBaseAddon` passes, and a meaningful UI change was seen in the browser.
- False greens: a broad run passing while the module alone fails (registry leak); the composed `web` typecheck passing while per-package `tsc` fails on a test file; stale `@angee/gql` until codegen reruns; PostgreSQL-only modules skipping silently on SQLite ([what CI runs](../../../docs/checks.md#what-ci-actually-runs)); CI never runs browser e2e ([e2e CI](../../../docs/frontend/e2e.md#ci)).

## Pitfalls

- A full run's file order can register a concrete model another module forgot; the standalone module run is the proof ([environment and checks](references/environment.md)).
- An abstract model imported while its app is absent keeps `app_label=None` for the whole process; the roster comment at `tests/settings.py:85-92` exists for that reason.
- Patch an inherited manager method on `type(manager)`, not the instance; `db_manager()` copies lose an instance patch ([environment and checks](references/environment.md)).
- A page rendered through the router must `findBy*` past the lazy boundary; importing the page component directly needs no wait ([page pitfalls](../angee-pages/references/pitfalls.md)).
- A shared pytest plugin must defer model imports until the registry is populated (`addons/angee/base/testing/permissions.py:19`, [what CI runs](../../../docs/checks.md#what-ci-actually-runs)).
- `composed_permissions` rewrites the synced schema with a forced overwrite into `tmp_path`; a module that edits a `.zed` on disk must do so under that fixture and never against the stack ([REBAC](../../../docs/backend/guidelines.md#rebac)).

## Read next

- [Environment and checks](references/environment.md)
- [Source-addon test models](../../../docs/checks.md#source-addon-test-models), [Frontend packages and addon fragments](../../../docs/checks.md#frontend-packages-and-addon-fragments), [Browser verification](../../../docs/checks.md#browser-verification)
- [GraphQL pitfalls](../angee-graphql/references/pitfalls.md), [authorization pitfalls](../angee-permissions/references/pitfalls.md), [Backend checks](../../../docs/backend/guidelines.md#checks)
- [Rendering and testing](../../../docs/frontend/guidelines.md#rendering-and-testing), [End-to-End Testing](../../../docs/frontend/e2e.md)
- [Web pitfalls](references/web-pitfalls.md): story and vitest traps moved from the frontend guide
- Skills: `angee-permissions`, `angee-graphql`, `angee-pages`, `angee-addon`, `slice`, `angee-workspace`

## Sources

<!--
Docs: AGENTS.md (Checks, Definition of Done); docs/guidelines.md (whole); docs/checks.md (whole);
docs/backend/guidelines.md #rules (test greps), #rebac, #pitfalls, #resource-loading, #development-server, #graphql-actor-and-write-contracts, #checks; docs/frontend/guidelines.md #rules, #authoring, #variants-and-impl, #context-aside, #rendering-and-testing, #form-save-contracts, #checks; docs/frontend/e2e.md (whole).
Code: addons/angee/base/testing/{__init__,fixtures,permissions}.py; addons/angee/provenance/testing/fixtures.py:1-40;
addons/angee/workflows/testing/__init__.py; addons/angee/tags/testing/{__init__,models}.py; tests/settings.py (whole);
tests/conftest.py:1-30,86-112,630-665,785-800,880-982; pyproject.toml:202-208; tests/test_tags.py:1-120,292-320,
412-480,598-657; tests/test_tags_field.py:1-160; tests/test_tags_mti.py; tests/test_messaging_resources.py:1-60;
tests/test_money.py:80-110; tests/test_resource_fixtures.py; tests/test_composed_note_workflow.py;
tests/test_notes_owner_adoption.py; tests/composed_host.py (grep); examples/addons/example/notes/tests/* (test_rebac,
test_iam_graphql in full; heads of the other seven); examples/e2e/{package.json,playwright.config.ts,tsconfig.json,
README.md,tests/auth.setup.ts,tests/notes.spec.ts,tests/addons-smoke.spec.ts,tests/fixtures/notes.ts,pages/notes-page.ts};
packages/e2e/src/{index,fixtures,pom,config,auth,graphql,record-access}.ts; packages/app/src/testing.tsx;
packages/app/config/{vitest,vitest-setup}.ts; packages/vitest.shared.ts; addons/angee/vitest.shared.ts; packages/ui and
packages/refine src/testing.tsx (exports); packages/storybook/{.storybook/main.mjs,scripts/catalog*.mjs,src/stories/runtime-fixtures.tsx};
addons/angee/tags/web/{vitest.config.ts,package.json,src/index.test.ts,src/views/TagsPage.test.tsx,
src/RecordTagsWidget.native.test.tsx}; addons/angee/money/web/src/widgets/money.test.tsx;
addons/angee/workflows/web/src/{index.test.ts,TriggersPage.stories.tsx,TriggersPage.native.test.tsx,testing.ts};
templates/addons/web/template/{vitest.config.ts,src/index.test.ts}.jinja; .github/workflows/reusable-checks.yml:60-80,
240-300,440-520; addons/angee/base/checks.py (E024 at :37).
Doc/code mismatches: examples/e2e/playwright.config.ts cites docs/testing/e2e.md and packages/e2e/src/{index,pom}.ts cite
docs/e2e.md; the file is docs/frontend/e2e.md. The consumer example ships no fragment tests (notes/web has only
typecheck). No doc owns a story-authoring guide.
-->
