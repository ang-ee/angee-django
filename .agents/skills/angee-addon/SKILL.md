---
name: angee-addon
description: "Creates and wires a new Angee addon's local declarations: level and folder, pyproject.toml, optional apps.py, autoconfig.py, app label, testing/models.py and the web fragment (defineBaseAddon, i18n namespace, pnpm workspace). Use for 'new addon', 'scaffold addon', 'add web fragment'."
---

# Angee Addon

Run `angee-design` first; it settles the level, vocabulary and owner map this skill encodes. This skill
wires declarations only; the reference example throughout is `examples/src/example/notes`.

## Use when

- "Create a new addon `angee.<name>` / consumer addon `<ns>.<name>`."
- "Scaffold `pyproject.toml`, `apps.py`, `autoconfig.py`, `testing/models.py`."
- "Add a web fragment / register the fragment in the pnpm workspace."
- Not for `settings.yaml`, `INSTALLED_APPS`, the host `web/package.json` or the stack profile: `angee-project`.

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `pyproject.toml` | Identity, dependencies, declared capability seams | `examples/src/example/notes/pyproject.toml` |
| `django_compose.components`, native `EntryPoint`, `Distribution`, `packaging.Requirement` | Component identity, requirement closure and package metadata | [Composition owners](../../../docs/backend/guidelines.md#composition-owners) |
| `angee.base.addons` | Packaged `[tool.angee.*]` tables and optional addon-module discovery | [Base adapter](../../../src/angee/base/addons.py) |
| `apps.py` (optional) | `name`, pinned `label`, `ready()` hooks, check registration | `src/angee/money/apps.py:17`, `src/angee/base/apps.py:22` |
| `autoconfig.py` | Settings fragments merged by native `django_compose.conf` in dependency order | `examples/src/example/notes/autoconfig.py:5`, `src/angee/base/autoconfig.py:23,42` |
| `testing/models.py`, `tests/settings.py`, `tests/conftest.py` | Concrete models and roster of the bare test host | `src/angee/tags/testing/models.py`, `tests/settings.py:29,144`, `tests/conftest.py:645` |
| `web/package.json`, `web/src/{index.tsx,i18n.ts,index.test.ts}` | The rendered fragment and its namespace | `examples/src/example/notes/web/`, `templates/addons/web/template/` |
| `src/angee/web/build.py` (`WebRuntime`), stack `pnpm-workspace.yaml` | Fragment discovery and `runtime/web/manifest.json`; which `web/` folders the JS install links | `src/angee/web/build.py:77,242`, `templates/stacks/dev/template/pnpm-workspace.yaml.jinja` |

## Reuse first

- Copy shapes from `examples/src/example/notes` (consumer, e2e target); `src/angee/tags` is the
  smallest framework addon, `src/angee/money` the one with checks in `ready()`.
- The [Base adapter](../../../src/angee/base/addons.py) reads packaged Angee tool tables and discovers optional
  addon modules. Capability owners interpret them: [Resources](../../../src/angee/resources/loader.py) owns resources,
  [GraphQL schema discovery](../../../src/angee/graphql/schema.py) owns schema references and
  [GraphQL ASGI](../../../src/angee/graphql/asgi.py) owns WebSocket contributions.
- Native django-compose owns component graphs, generic lifecycle and optional hook dispatch:
  `django_compose.conf` merges settings, `django_compose.urls` reads explicit `AppConfig.compose_urls`,
  and `django_compose.asgi` composes `compose_asgi()` contributions. Declare through those owners.
- `defineBaseAddon`, `resourcePageRoutes` (`packages/app/src/define-base-addon.ts:162,66`), `createNamespaceT`
  (`packages/ui/src/i18n/index.ts:45`), `expectValidBaseAddon` (`packages/app/src/testing.tsx:101`),
  `defineAngeeWebVitestConfig` (`packages/app/config/vitest.ts:90`) own fragment composition.
- Start an addon from `templates/addons/web` (Copier: manifest, model, gate, schema, demo row, test model, test and, with `include_web`, the fragment under `web/`), not from an older addon ([web build pitfalls](../angee-framework/references/web-pitfalls.md)).

## Steps

1. **Choose the level and folder.** Framework capability → `src/angee/<name>` (package `angee.<name>`;
   `src/angee/` has no `__init__.py`). Product logic → the consumer repo's `src/<ns>/<name>` (notes:
   `examples/src/example/notes`, package `example.notes`). Short, lowercase, or `<base>_<qualifier>` for an
   integration. **Owner rule:** [Repository Role](../../../AGENTS.md#repository-role) decides the level; the
   folder name becomes the permanent Django label (step 3).

2. **Write the native package declaration.** Each addon is one distribution. Its
   app entry point supplies identity; requirements supply installation and addon
   activation. Capability-specific facts belong to their own tool tables:

   ```toml
   build-system = { requires = ["hatch-angee>=1.1,<2"], build-backend = "hatch_angee" }
   tool.angee.platform.category = "Example"
   [project]
   name = "example-notes"
   version = "1.0.0"
   description = "Notes example"
   dependencies = ["angee-base>=3,<4", "angee-iam>=3,<4", "angee-resources>=3,<4"]
   dynamic = ["entry-points"]
   [tool.angee.resources]
   demo = [{ path = "demo/020_notes.note.yaml", depends_on = "angee.iam:demo/010_iam.user.yaml" }]
   ```

   Only requirements whose markers are true with `extra=""` activate addon apps.
   `apps.py` declares foreign Django app edges through django-compose's
   `requires_apps` extension; it carries no
   copy of addon requirements. GraphQL overrides live under `tool.angee.graphql`;
   permission paths belong to `AppConfig.rebac_schema`. MCP uses its Python
   contribution owner rather than redundant tool metadata. Resource paths are
   relative to `resources/`; the resource owner preserves canonical ledger paths.
   Describe required data upgrades for existing projects; projects author them
   in native `runtime/<label>/migrations/` with explicit dependencies. Web identity
   and external codegen belong
   to `web/package.json` (`name`, `angee.codegen`).
   **Owner rule:** package metadata owns requirements, capability owners interpret
   their tool tables, and Django owns app lifecycle
   ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).

   A library is explicitly packaged without an `angee.addons` entry point; do not
   infer library identity from missing `apps.py`. The published
   [hatch-angee backend](https://github.com/ang-ee/hatch-angee#readme) owns addon
   layout and metadata defaults and delegates native builds to Hatchling. Use
   `dynamic = ["entry-points"]` for ordinary addons; retain explicit entry points
   only for custom mappings or additional groups described by that owner. Readers
   consume prepared or installed distribution metadata, never infer identity from
   an omitted source table.

3. **Add `apps.py` only for native customization.** Notes has none. Add one for checks,
   signals or a pinned label (shape: `src/angee/money/apps.py:17`):

   ```python
   class MoneyConfig(AppConfig):
       default = True
       name = "angee.money"            # must equal the app entry-point key
       # label = "base"                # only to pin an already persisted label (base/apps.py:22)

       def ready(self) -> None:
           super().ready()
           _register_checks()          # once per process; the check imports models lazily
   ```

   The label defaults to the folder name and is permanent: migrations, content types,
   `rebac_resource_type = "<label>/<model>"` and the fragment id key on it. **Owner rule:** `AppConfig`
   owns Django identity and lifecycle only ([Django-native rule](../../../docs/backend/guidelines.md#django-native-rule)).

4. **Add `autoconfig.py` when the addon contributes settings.** A literal `SETTINGS` dict of
   dotted strings, no addon imports; dotted keys set nested entries, `:append` merges into a list.

   ```python
   """Settings fragments contributed by the example Notes addon."""

   SETTINGS = {
       "ANGEE_WORKFLOW_STEP_CLASSES.note_publish": "example.notes.steps.PublishNote",
       "ANGEE_WORKFLOW_STEP_CLASSES.note_await_review": "example.notes.steps.AwaitNoteReview",
   }
   ```

   Implementation contributors add to the base's named setting; bound fields validate their mapping.
   A rowless contract owner registers a stable native Django system check using `check_impl_registry`. If a value derives from the host → a `settings(namespace)` hook reading only
   its namespace (`src/angee/base/autoconfig.py:42`). Contribute only what the addon owns: `angee.base`
   already appends `PermissionIntegration` (`src/angee/base/autoconfig.py:24`); a second registration is refused
   (`django_compose.declarations.resolve_integrations`). **Owner rule:**
   [Autoconfig](../../../docs/composer.md#autoconfig) owns merge semantics; settings loading never imports models.

   

5. **Register test models.** Framework addons add `testing/__init__.py` and `testing/models.py`: one
   concrete subclass per abstract source model (`abstract = False`, `app_label = "<label>"`, `db_table =
   "test_<label>_<model>"`, same `rebac_resource_type`); list the addon in `tests/settings.py` `INSTALLED_APPS`
   (`:29`) and import the module once in `tests/conftest.py` (`# noqa: F401`, `:645`); tests go in
   `tests/test_<name>*.py` (`angee-tests` owns their shapes). A consumer addon keeps `tests/` inside the addon (notes:
   `examples/src/example/notes/tests/`; `pyproject.toml:205` puts `examples/src` on the test path).
   **Owner rule:** [source-addon test models](../../../docs/checks.md#source-addon-test-models) own
   adoption; never register a second concrete model for a resource type.

6. **Register the web fragment** (only when the addon renders). `templates/addons/web` rendered with `addon_id=<label>`
   and `include_web=true` emits it under `web/`; then adapt. `web/package.json`: name `@angee/<label>` (framework) or `@<ns>/<label>-web` (notes:
   `@angee-example/notes-web`), private, `exports: { ".": "./src/index.tsx" }`, `@angee/app` and `@angee/ui` as `workspace:*`.

   ```tsx
   // web/src/index.tsx — one greppable seam per addon (examples/src/example/notes/web/src/index.tsx)
   import { defineBaseAddon } from "@angee/app";
   import { enNotesMessages } from "./i18n";

   const notes = defineBaseAddon({
     id: "notes",                                   // == Django label
     i18n: { notes: enNotesMessages },             // namespace == id, never "ui"
     // routes, menus, icons, forms, containers: authored by angee-pages / angee-forms
   });
   export default notes;
   ```

   `i18n.ts` exports the bundle and `useNotesT = createNamespaceT("notes", enNotesMessages)` (`web/src/i18n.ts:28`);
   `tsconfig.json` extends `src/angee/tsconfig.base.json` by relative path. The template's `index.test.ts`
   calls `expectValidBaseAddon(addon)`; its `vitest.config.ts` passes `gqlAlias: []` until the fragment consumes
   `@angee/gql`. The stack's `pnpm-workspace.yaml` globs match `src/angee/*/web`, stack-local `src/*/*/web`
   and, under the full profile, `examples/src/*/*/web`; a fragment in another repository slot needs one explicit
   line there. `WebRuntime` discovers the fragment from `web/package.json`; the package's `angee.codegen` object declares external SDL/documents. Routes, menus and pages are authored with `angee-pages` (`resourcePageRoutes`), never hand-written here.
   **Owner rule:** the fragment registers the addon's id and namespace once; page routing belongs to the
   pages skill's shared helper ([frontend rules](../../../docs/frontend/guidelines.md#rules)).

7. **Hand off host wiring.** `INSTALLED_APPS`, `settings.yaml`, the host `web/package.json` dependency and the stack profile belong to `angee-project`.

## Do not

- A package fact (requirements, capability paths, a label that merely repeats the folder name) mirrored on `AppConfig`: the manifest owns it and installed entry points establish identity (native installed index); `label` belongs on `AppConfig` only to pin an already persisted label (`src/angee/base/apps.py:22`) ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).
- An addon-local registry, loader or settings parser for a fact native `django_compose.conf` or the manifest readers already read: the existing owner takes one explicit line instead ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).
- A second registration of a framework-owned integration such as `PermissionIntegration` when the base already appends it (`src/angee/base/autoconfig.py:24`; duplicates refused at `django_compose.declarations.resolve_integrations`).
- Model or `django.conf.settings` imports at `apps.py`/`autoconfig.py` module scope: settings merge runs before apps are ready, so only `ready()` and `settings(namespace)` may import ([Autoconfig](../../../docs/composer.md#autoconfig)).
- A module named for a noun (`statements.py`) when the concern already has an owning module (`models.py`, `managers.py`, a verb module) ([Naming](../../../docs/backend/guidelines.md#naming)).
- Customer, deployment or product names in framework-level code or fixtures; a consumer addon names its own product ([naming](../../../docs/guidelines.md#name-so-code-can-be-found-not-guessed)).
- Caveated REBAC subjects in `permissions.zed`: actor-scoped querysets have no caveat context, so `angee.E024` rejects them (`src/angee/base/checks.py:37`); use field-backed conditional relations.
- An addon bundle under the `ui` i18n namespace or keys that override shared copy: the addon namespace equals its id and `ui` is reserved ([reserved namespace](../../../docs/frontend/guidelines.md#reserved-translation-namespace)).
- A manifest typed by annotation (`const x: BaseAddon = {…}`) instead of `defineBaseAddon`, which owns validation of the declared shape ([frontend rules](../../../docs/frontend/guidelines.md#rules)).

## Verify

Done means: package metadata parses, the app entry-point key agrees with its native config, the bare host imports with the addon listed; the
fragment's `index.test.ts` passes; `runtime/web/manifest.json` (`src/angee/web/build.py:149`) lists the package.

- In a lane (jj workspace: no host, database or JS install): focused pytest, ruff and mypy on touched files,
  `tests/test_layering.py`, `tests/test_base_layering.py` — [Python and composition contracts](../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: composition drift, runtime checks, SDL, codegen, composed and per-package typecheck,
  vitest and the architecture guardrail — [composition and schema](../../../docs/checks.md#composition-and-schema),
  [frontend packages and addon fragments](../../../docs/checks.md#frontend-packages-and-addon-fragments).
- Loop: `.agents/skills/slice/SKILL.md`. False greens: the composed-fragment typecheck row does not replace the
  per-package row, and root test/typecheck scripts do not perform codegen, so `@angee/gql` is stale until it reruns
  ([what CI actually runs](../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- `pnpm install` or a bare `pnpm exec` inside a slot forks every linked framework package's identity; the stack root owns the JS install ([choose the working directory](../../../docs/checks.md#choose-the-working-directory)).
- A passing composed `web` typecheck says nothing about a fragment's test files; CI runs the per-package row ([frontend checks](../../../docs/checks.md#frontend-packages-and-addon-fragments)).
- A noun-named module beside `models.py` fails review outright ([Naming](../../../docs/backend/guidelines.md#naming)).
- Customer and product names in framework tests or fixtures are a defect ([name so code can be found](../../../docs/guidelines.md#name-so-code-can-be-found-not-guessed)).
- A manifest fact mirrored on `AppConfig` drifts; the binding is validated once (`native installed index`, [composition owners](../../../docs/backend/guidelines.md#composition-owners)).
- An invented list or marker loses to one explicit line in the existing owner ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).

## Read next

- [Addon declarations](../../../docs/composer.md#addon-declarations), [Addon discovery](../../../docs/composer.md#addon-discovery), [Glossary: Composition](../../../docs/glossary.md#composition)
- [Django-native rule](../../../docs/backend/guidelines.md#django-native-rule), [Package layering](../../../docs/backend/guidelines.md#package-layering), [Stack](../../../docs/stack.md)
- [Frontend package layering](../../../docs/frontend/guidelines.md#package-layering), [Ownership boundaries](../../../docs/frontend/guidelines.md#ownership-boundaries)
- Extending an engine instead of declaring a new addon: the capability guide beside that engine, loaded by path — [mcp-tools](../../../src/angee/mcp/skills/mcp-tools/SKILL.md), [jobs-tasks](../../../src/angee/jobs/skills/jobs-tasks/SKILL.md), [integrate-bridges](../../../src/angee/integrate/skills/integrate-bridges/SKILL.md), [workflow-definitions](../../../src/angee/workflows/skills/workflow-definitions/SKILL.md), [provenance-attribution](../../../src/angee/provenance/skills/provenance-attribution/SKILL.md), [agent-composition](../../../src/angee/agents/skills/agent-composition/SKILL.md).
- Route: `angee-design` → `angee-addon` → `angee-models` + `angee-permissions` → `angee-graphql` + `angee-resources` → `angee-forms` + `angee-pages`, with `angee-tests` beside each artifact, then `angee-project`, `angee-runtime` and acceptance through `slice`.
