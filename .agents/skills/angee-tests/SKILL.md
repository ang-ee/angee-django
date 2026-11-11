---
name: angee-tests
description: "Authors an Angee addon's tests: domain contracts (transitions, verb outcomes, invariants, Hypothesis properties), the permission decision table and its enforcement points, bare-host test models, resource loads, schema-driven GraphQL tests, vitest fragment tests, story fixtures and e2e specs. Use for 'write tests for <addon>', 'add e2e'."
---

# Angee Tests

This skill authors tests; running them, their roots and CI coverage belong to `docs/checks.md`. Tests exist to catch the defects that escape: most are domain logic (state machines, order-dependent routing, cursors), few are pure permission. Spend the tests there.

## Use when

- "Write / add tests for the `<addon>` addon", "cover `<Model>`'s lifecycle", "add a regression test for <bug>".
- "Add a GraphQL test for the `<resource>` root or the `<verb>` mutation", "prove the query budget".
- "Test the `<addon>` web fragment / `<Page>` / `<widget>`", "add a story fixture", "add an e2e spec for `/<route>`".
- Not for selecting or running checks: `docs/checks.md` owns commands and roots.

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/testing/models.py`, `tests/settings.py`, `tests/conftest.py` | concrete models of the bare host, the app roster, one-time registration | `src/angee/tags/testing/models.py`, `INSTALLED_APPS` in `tests/settings.py` |
| `src/angee/base/testing/fixtures.py`, `permissions.py` | `django_db_setup`, `composed_tables`; `composed_permissions` (`permissions.py`) | module docstrings |
| `tests/conftest.py` | actors and schema drivers: `create_user`, `create_platform_admin`, `vault_for`, `addon_schema` | `tests/test_tags.py` |
| `src/angee/graphql/testing/documents.py` | document execution as an actor and statement ceilings: `execute_schema`, `result_data`, `measure_graphql_document` | [GraphQL document budgets](../../../docs/checks.md#graphql-document-budgets) |
| `tests/test_<addon>*.py` (framework) / `<addon>/tests/test_*.py` (consumer) | the test modules, one per concern | `tests/test_work_cycles.py`, `examples/src/example/notes/tests/` |
| `tests/composed_host.py` | the composed-host subprocess that runs consumer `TransactionTestCase` modules in the framework suite | `test_composed_note_workflow` in `tests/test_composed_note_workflow.py` |
| `packages/refine/src/testing-wire.ts` | `graphqlWireFetch`, the GraphQL wire fake: one handler per root field, loud on an unhandled field or a missing required variable | `src/angee/spaces/web/src/SpacesPage.native.test.tsx` |
| `packages/app/src/testing.tsx`, `packages/ui/src/testing.tsx` | `expectValidBaseAddon`, `chromeSnapshotForRoute`, `TEST_SCHEMAS`; `createUiTestProviders` (`ui`) | `src/angee/tags/web/src/index.test.ts` |
| `packages/app/config/vitest.ts`, `src/angee/vitest.shared.ts` | `defineAngeeWebVitestConfig`, `gqlAliasFor`; the addon-tree `gqlAlias` | `src/angee/tags/web/vitest.config.ts` |
| `packages/storybook/src/stories/runtime-fixtures.tsx`, `.storybook/main.mjs`, `scripts/catalog.test.mjs` | `storySchema` (a schema config over refine's GraphQL wire fake), `RuntimeFixture`; addon story discovery; token-drift scan over `src/` | `src/angee/workflows/web/src/TriggersPage.stories.tsx` |
| `packages/e2e/src/` | `test`/`api` (`fixtures.ts`), `defineE2EConfig` (`config.ts`), `loginViaApi` and `roleStatePath` (`auth.ts`), `PageObject` (`pom.ts`) | `examples/e2e/` |

## Reuse first

- Database: native `db` (`composed_tables` is its compatibility alias) or `composed_permissions` (`angee.base.testing`); never a private `rebac sync` or table fixture.
- Generated cases: `pytest.param(..., id=...)` tables for named examples; Hypothesis (`given`, `example`, `RuleBasedStateMachine`, dev group) for properties and state machines, never a hand-rolled random loop.
- Actors: `create_user`, `create_platform_admin`, `rebac.actor_context` / `system_context`, `rebac.roles.grant`, a grantee through the model's `grant_record_access`.
- Stand-in records from owning test apps: `angee.messaging.testing.models` (`Party`, `Handle`), `angee.projects.testing.models` (`Task`, `Queue`, `Cycle`), `tests.conftest` (`Page`, `File`, `Need`, `vault_for`); `tests/mtidemo`, `hierdemo`, `recordrefdemo` for structural probes.
- Workflow and decision states: `angee.workflows.testing.drivers` (`load_workflow`, `start_run`, `run_until`, `decide`), never fabricated step runs ([source-addon test models](../../../docs/checks.md#source-addon-test-models)).
- PostgreSQL lock races: `submit` and `wait_for_lock` (`tests/lock_waits.py`), never a private polling loop.
- Query budgets: Django `CaptureQueriesContext` at 1 and 25 rows; a document's statement ceiling through `measure_graphql_document`.
- Frontend: `@angee/app/testing`, `@angee/ui/testing`, `@angee/refine/testing`, `@angee/metadata/testing`, `@angee/storybook/testing`, Testing Library. Browser: `@angee/e2e` only, Page Objects under `pages/`.

## Steps

1. **Map the set before writing.** Backend: the domain contract of each owner (step 4), the decision table and enforcement points (step 5), resource load, GraphQL roots and budget, one module per shipped seam (steps, commands, MCP tools). Web: `index.test.ts`, page declarations, `.native.test.tsx` provider tests, widgets, stories; browser: a Page Object and specs. Name modules `test_<concern>.py` and tests `test_<behavior>`. Before adding a test, find the owner test of the same contract: one rule is proved once, at its owner, not again at every layer above it. **Owner rule:** one concern per module so focused selection and discovery agree ([source-addon test models](../../../docs/checks.md#source-addon-test-models)).

2. **Register concrete models (framework addon only).** The bare host runs no composer, so each abstract source model needs one concrete subclass in an installed app, imported once from the root conftest before database setup (`TagAssignment`, and the const-backed `TagRole` with `managed = False`, in `src/angee/tags/testing/models.py`). A consumer addon has no test models: its tests run on the composed host (step 3). **Owner rule:** Django owns static test-table lifecycle; one concrete model per resource type ([environment and checks](references/environment.md)).

3. **Pick the database contract per module.** Request `db`; REBAC decides over the session baseline, which already binds and synchronizes every addon's merged `permissions.extends.zed`. `composed_tables` is a compatibility alias of `db` kept for consumer suites. `composed_permissions` binds a test-owned copy for a test that edits a `.zed`. Each test rolls back. Mark `django_db(transaction=True)` only for real commit semantics: another connection or thread, a lock race, a vendor check. A changeset or `on_commit` effect runs through `django_capture_on_commit_callbacks(execute=True)` (`test_a_party_merge_moves_what_points_at_the_merged_party_and_revert_restores_it` in `tests/test_parties_circles.py`). Consumer modules are `TransactionTestCase` classes driven through `tests/composed_host.py` (`NotesAuthorizationTests` in `examples/src/example/notes/tests/test_rebac.py`). Mark PostgreSQL-only modules `pytest.mark.skipif(connection.vendor != "postgresql", …)`. **Owner rule:** pytest-django and the base plugin own setup, rollback and flush; `--nomigrations` and PostgreSQL settings belong to the CI selection, never to a module ([Python contracts](../../../docs/checks.md#python-and-composition-contracts)).

4. **Prove the domain contract on its owner.** Call the manager or model verb, never a raw `create` standing in for it, and assert outcomes: every transition including each illegal one and its refusal, idempotent reruns, invariants and constraints. Name each table row by the rule it pins.

   ```python
   @pytest.mark.parametrize(("stances", "declared", "outcome"), [
       pytest.param([("p", P, "P", {"confidence": 0.4}), ("s", SOR, "S", {})], {}, ("P", ["p"]), id="precedence"),
       pytest.param([("p", P, "", {"snak": "no_value", **_PREFERRED})], {}, ("", ["p"]), id="dismissal-clears"),
   ])                                                    # tests/test_statement_records.py
   ```

   Where order, time or interleaved transitions matter, add a Hypothesis property or `RuleBasedStateMachine` against a plain-Python oracle: insertion order never changes a winner (`test_insertion_order_never_changes_the_survivor`), a cadence stays contiguous as the clock advances (`test_a_rolling_cadence_stays_contiguous_and_closes_exactly_the_ended_windows` in `tests/test_work_cycles.py`). Each example rolls back its own rows; pin a known boundary with `@example`.

   ```python
   @settings(deadline=None, max_examples=20)
   @given(weeks=st.integers(1, 3), as_of=st.dates(date(2026, 1, 1), date(2026, 12, 31)))
   def test_a_rolling_cadence_stays_contiguous(weeks: int, as_of: date) -> None:
       with transaction.atomic(), system_context(reason="tests.work.cycles"):
           ...                                           # write, act, assert
           transaction.set_rollback(True)                # the next example starts clean
   ```

   A test taking a function-scoped fixture (`db`, `monkeypatch`) adds `suppress_health_check=[HealthCheck.function_scoped_fixture]`; the per-example rollback is what makes that safe. **Owner rule:** the owner's docstring states the contract and the test asserts it as outcomes ([Persistence primitives](../../../docs/backend/guidelines.md#persistence-primitives), [reconcile](../../../docs/guidelines.md#reconcile-code-docs-and-tests)).

5. **Prove permissions as the decision table plus enforcement points.** The addon's policy is proved once, as data: the decision table lists actors and their grants, each row the expected `check` and `accessible` answer, run through django-zed-rebac's decision-table assertion over one rolled-back world (a parametrized `has_access` table where the locked version predates it). Then one test per enforcement point the addon owns, with two or three actors (an owner or grantee, an outsider, an admin where a const-backed role applies): queryset scope (`objects.with_actor(actor)`, `scoped_for_aggregate()`), the `has_access` point check, the create preflight (`check_new`), a field gate, subscription revocation (`ChangeReadGate` in `src/angee/graphql/access.py`), and the code that writes grants, run as the verb rather than seeded tuples. **Owner rule:** `permissions.zed` owns the rule; an enforcement point proves it is wired, the table proves the rule ([authorization pitfalls](../angee-permissions/references/pitfalls.md)).

6. **Load declared resources.** Framework: a throwaway addon loading one tier (`Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])` in `tests/test_messaging_resources.py`); consumer: the demo tier loads twice with the same rows (`test_demo_load_is_idempotent` in `test_rebac.py`). `tests/test_resource_fixtures.py` already validates every declared value; do not repeat it. **Owner rule:** the loader owns xref and tier semantics; a test asserts rows and grants, not file parsing ([Resource loading](../../../docs/backend/guidelines.md#resource-loading)).

7. **Drive GraphQL through the schema.** Bare host: `execute_schema(addon_schema(<addon>.schemas, "console"), document, variables, user=actor)`; composed host: the login mutation and a `Client` POST (`test_owner_gated_flag_is_not_an_aggregate_group_by` in `examples/src/example/notes/tests/test_iam_graphql.py`). Cover a non-admin reader, the denied anonymous write with its code, and an identical query count at 1 and 25 rows; snapshot resource metadata through `GraphQLSdl.from_discovery().render_metadata()`. **Owner rule:** resolvers are thin callers; test the schema contract, never a resolver function ([GraphQL pitfalls](../angee-graphql/references/pitfalls.md)).

8. **Test the web fragment.** `web/vitest.config.ts` is `defineAngeeWebVitestConfig({ gqlAlias })`; `src/index.test.ts` asserts `expectValidBaseAddon` and the containers. Page declarations: call the page as a function and parse `pageChildren`/`parsePageColumns` (`TagsPage.test.tsx`). Providers: `.native.test.tsx` under happy-dom with `createUiTestProviders` (`RecordTagsWidget.native.test.tsx`). Stories export a fixture fed by `storySchema({ <root field>: handler })`, reused by the native test, which matches requests by `fields` or `operationName` (`TriggersPage.native.test.tsx`). **Owner rule:** `@angee/app` owns the Vitest defaults and the manifest contract ([frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments), [story fixtures](references/web-pitfalls.md)).

9. **Add browser specs.** A consumer e2e package holds `playwright.config.ts` (`defineE2EConfig()`), `tests/auth.setup.ts`, Page Objects in `pages/<route>-page.ts` extending `PageObject`, and `tests/<concern>.spec.ts` using `roleStatePath`. Mutating specs own their rows through a `test.extend` fixture (`examples/e2e/tests/fixtures/notes.ts`) and assert invariants, never seed counts. **Owner rule:** the stack owns data, `@angee/e2e` owns login and transport, the Page Object owns selectors ([Page Object Model](../../../docs/frontend/e2e.md#authoring-style-page-object-model)).

## Do not

- Write a private REBAC-sync or table fixture beside `db`/`composed_tables`/`composed_permissions`, or create tables by hand instead of `tests/tables.py` `model_tables` ([environment and checks](references/environment.md)).
- Mark a module transactional for convenience, or let Hypothesis examples share rows.
- Enumerate every actor against every permission in example tests: that is the decision table's job, once per addon.
- Re-prove one rule at the model, GraphQL and composed levels, or re-prove a library's guarantee.
- Seed grants as tuples to test the code that writes them.
- Ship admin-only GraphQL tests, or `force_login` without `backend="angee.iam.auth.ModelBackend"` ([GraphQL pitfalls](../angee-graphql/references/pitfalls.md)).
- Add a caveated subject to make a conditional grant testable; use a field-backed relation (`angee.E024`).
- Assert seed row counts, prescribed words in a file, or a spy's arguments instead of an outcome at a boundary, or add harness serialization to hide a locking failure ([proven practices](../../../docs/guidelines.md#follow-proven-best-practices-and-patterns)).
- Import `test`/`expect` from `@playwright/test`, or copy provider setup instead of `createUiTestProviders`.
- Weaken or skip a failing test to make a check green ([reconcile](../../../docs/guidelines.md#reconcile-code-docs-and-tests)).

## Verify

Commands and roots are in [Python contracts](../../../docs/checks.md#python-and-composition-contracts), [Frontend packages](../../../docs/checks.md#frontend-packages-and-addon-fragments) and [Browser verification](../../../docs/checks.md#browser-verification); the loop is `.agents/skills/slice/SKILL.md`.

- In a jj lane (no host, database or JS install): each changed Python module alone, then with the addon's other modules; ruff and mypy on touched files; the layering tests. Only on integrated `src`: composed-host runs, per-package `typecheck`, vitest per fragment, `catalog:check`, the browser suite, and codegen first after a schema change.
- Done means: every transition, including each illegal one, and every verb outcome is asserted on its owner, with a property or state machine where order or time decides; a fixed defect ships a regression test that fails before the fix; the decision table passes and each enforcement point has its test; GraphQL covers the denied anonymous write and the 1/25-row budget; `expectValidBaseAddon` passes; a meaningful UI change was seen in the browser.
- False greens: a broad run passing while the module alone fails (registry leak); a property whose sort keys tie, so the database's row order decides; the composed `web` typecheck passing while per-package `tsc` fails; stale `@angee/gql` until codegen reruns; PostgreSQL-only modules skipping silently on SQLite ([what CI runs](../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- An abstract model imported while its app is absent keeps `app_label=None` for the whole process; the roster comment in `INSTALLED_APPS` of `tests/settings.py` exists for that reason.
- Script a boundary with its owner's fake, never patch internals: `mock_transport` and `request_fields` (`src/angee/integrate/testing/http.py`) for outbound HTTP, `OidcProvider` (`src/angee/iam_integrate_oidc/testing/provider.py`) for OAuth and OIDC, `ImapServer` and `imap_server` (`src/angee/messaging_integrate_imap/testing/server.py`) for IMAP, `connection.execute_wrapper` for a database failure. `tests/test_patch_targets.py` refuses patches of underscore names, of `type(<model, manager or queryset>)` and of the workflow runner ([environment and checks](references/environment.md)).
- Under Python 3.14 a `functools.partial` stored as a class attribute binds as a method; wrap it in `staticmethod` (`serve` in `src/angee/messaging_integrate_imap/testing/server.py`).
- Under a rolled-back test a changeset closes only when its `on_commit` callbacks run; a revert test captures them.
- A Hypothesis property over database rows must vary what the database orders by: rows named or keyed in the generated order hide an order dependence behind an index.
- A shared pytest plugin must defer model imports until the registry is populated (`installed_field_owners` in `src/angee/base/testing/permissions.py`).
- A page rendered through the router must `findBy*` past the lazy boundary ([page pitfalls](../angee-pages/references/pitfalls.md)).
- `composed_permissions` binds a test-owned copy of the merged inputs under `tmp_path`; a test that edits a `.zed` on disk edits that copy and synchronizes the edit itself, never the session's files or the stack ([REBAC](../../../docs/backend/guidelines.md#rebac)).

## Read next

- [Environment and checks](references/environment.md), [Web pitfalls](references/web-pitfalls.md)
- [Source-addon test models](../../../docs/checks.md#source-addon-test-models), [Rendering and testing](../../../docs/frontend/guidelines.md#rendering-and-testing), [End-to-End Testing](../../../docs/frontend/e2e.md)
- [GraphQL pitfalls](../angee-graphql/references/pitfalls.md), [authorization pitfalls](../angee-permissions/references/pitfalls.md), [Backend checks](../../../docs/backend/guidelines.md#checks)
- Skills: `angee-permissions`, `angee-graphql`, `angee-pages`, `angee-addon`, `slice`, `angee-workspace`

## Sources

<!--
Docs: AGENTS.md (Checks, Definition of Done); docs/guidelines.md; docs/checks.md; docs/backend/guidelines.md #rebac,
#resource-loading, #graphql-actor-and-write-contracts, #checks; docs/frontend/guidelines.md #rendering-and-testing;
docs/frontend/e2e.md.
Code: src/angee/base/testing/{fixtures,permissions}.py; src/angee/provenance/testing/fixtures.py (captured_changeset);
src/angee/provenance/context.py (boundaries close on commit); src/angee/graphql/access.py (ChangeReadGate);
src/angee/workflows/testing/__init__.py; src/angee/tags/testing/models.py; tests/settings.py; tests/conftest.py;
tests/test_statement_records.py; tests/test_work_cycles.py; tests/test_parties_circles.py; tests/test_parties_suggestions.py;
tests/test_tags.py; tests/test_messaging_resources.py; tests/test_resource_fixtures.py; tests/composed_host.py;
examples/src/example/notes/tests/{test_rebac,test_iam_graphql}.py; examples/e2e/; packages/e2e/src/;
packages/app/src/testing.tsx; packages/app/config/vitest.ts; src/angee/vitest.shared.ts;
packages/storybook/src/stories/runtime-fixtures.tsx; src/angee/tags/web/; src/angee/workflows/web/src/.
-->
