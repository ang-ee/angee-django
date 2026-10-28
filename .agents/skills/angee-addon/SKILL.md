---
name: angee-addon
description: "Creates and wires a new Angee addon's local declarations: level and folder, addon.toml, optional apps.py, autoconfig.py, app label, testing/models.py and the web fragment (defineBaseAddon, i18n namespace, pnpm workspace). Use for 'new addon', 'scaffold addon', 'add web fragment'."
---

# Angee Addon

Run `angee-design` first; it settles the level, vocabulary and owner map this skill encodes. This skill
wires declarations only; the reference example throughout is `examples/addons/example/notes`.

## Use when

- "Create a new addon `angee.<name>` / consumer addon `<ns>.<name>`."
- "Scaffold `addon.toml`, `apps.py`, `autoconfig.py`, `testing/models.py`."
- "Add a web fragment / register the fragment in the pnpm workspace."
- Not for `settings.yaml`, `INSTALLED_APPS`, the host `web/package.json` or the stack profile: `angee-project`.

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `addon.toml` | Identity, dependencies, declared capability seams | `examples/addons/example/notes/addon.toml` |
| installed `hatch_angee/manifest.py` | The full key set and validation (`AddonManifest`, `parse_manifest`) | `hatch_angee/manifest.py:33,189` |
| `angee/addons.py` | Manifest-to-`AppConfig` binding, `name` agreement, optional-module and dotted-reference conventions | `angee/addons.py:19,73,79,91` |
| `apps.py` (optional) | `name`, pinned `label`, `ready()` hooks, check registration | `addons/angee/money/apps.py:17`, `addons/angee/base/apps.py:22` |
| `autoconfig.py` | Settings fragments merged by `AutoConfig` in dependency order | `examples/addons/example/notes/autoconfig.py:5`, `addons/angee/base/autoconfig.py:23,42` |
| `testing/models.py`, `tests/settings.py`, `tests/conftest.py` | Concrete models and roster of the bare test host | `addons/angee/tags/testing/models.py`, `tests/settings.py:29,144`, `tests/conftest.py:645` |
| `web/package.json`, `web/src/{index.tsx,i18n.ts,index.test.ts}` | The rendered fragment and its namespace | `examples/addons/example/notes/web/`, `templates/addons/web/template/` |
| `addons/angee/web/build.py` (`WebRuntime`), stack `pnpm-workspace.yaml` | Fragment discovery and `runtime/web/manifest.json`; which `web/` folders the JS install links | `addons/angee/web/build.py:77,242`, `templates/stacks/dev/template/pnpm-workspace.yaml.jinja` |

## Reuse first

- Copy shapes from `examples/addons/example/notes` (consumer, e2e target); `addons/angee/tags` is the
  smallest framework addon, `addons/angee/money` the one with checks in `ready()`.
- `angee.addons.optional_addon_module` / `resolve_addon_reference` own conventional discovery (`schema.py`,
  `permissions.zed`, `mcp_tools.py`, `urls.py`, `asgi.py`); `AutoConfig` (`angee/compose/autoconfig.py:29`)
  owns settings merge. Declare, never load.
- `defineBaseAddon`, `resourcePageRoutes` (`packages/app/src/define-base-addon.ts:162,66`), `createNamespaceT`
  (`packages/ui/src/i18n/index.ts:45`), `expectValidBaseAddon` (`packages/app/src/testing.tsx:101`),
  `defineAngeeWebVitestConfig` (`packages/app/config/vitest.ts:90`) own fragment composition.
- Start a fragment from `templates/addons/web` (Copier), not from an older addon ([frontend pitfalls](../../../docs/frontend/guidelines.md#pitfalls)).

## Steps

1. **Choose the level and folder.** Framework capability → `addons/angee/<name>` (package `angee.<name>`;
   `addons/angee/` has no `__init__.py`). Product logic → the consumer repo's `addons/<ns>/<name>` (notes:
   `examples/addons/example/notes`, package `example.notes`). Short, lowercase, or `<base>_<qualifier>` for an
   integration. **Owner rule:** [Repository Role](../../../AGENTS.md#repository-role) decides the level; the
   folder name becomes the permanent Django label (step 3).

2. **Write `addon.toml`.** The marker file is what makes a Django app an addon. Every addon whose
   models, seeds or settings it names appears in `depends_on` (`notes/addon.toml:8-24`; abbreviated):

   ```toml
   [addon]
   name = "example.notes"                   # == AppConfig.name; validated by angee.addons
   description = "A notes example — the minimal consumer addon the example project composes"
   keywords = ["notes", "example"]
   category = "Example"                     # optional catalogue grouping (platform.Addon)
   depends_on = ["angee.base", "angee.iam", "angee.integrate", "angee.workflows", "angee.provenance", "angee.web"]
   dependencies = ["faker>=30.0"]           # PEP 508; projected into the host's generated group

   [resources]
   master = [{ path = "resources/master/010_integrate.vendor.yaml", adopt = "slug" }]   # needs angee.integrate above
   demo = [{ path = "resources/demo/020_notes.note.yaml", depends_on = "iam:resources/demo/010_iam.user.yaml" }]
   ```

   Recipe: `addon.name` is required; `description`, `keywords`, `category`, `depends_on` (addon
   names and plain Django apps; include `angee.web` when shipping `web/`) and `dependencies` are
   the identity keys; `[resources]` tiers list `path` entries with `adopt` and `depends_on`
   (`<label>:…` names a cross-addon file; `angee-resources` owns the rows). The full key set and its
   validation belong to the installed `hatch_angee/manifest.py` (`AddonManifest`:33, `parse_manifest`:189).
   Declare an optional seam only to override its conventional file; each owner reads its own: `schemas`
   (`addons/angee/graphql/schema.py:656`), `permissions` (`addons/angee/base/build.py:133`),
   `[web]` (`addons/angee/web/build.py:242`), `mcp.tools` (`addons/angee/mcp/server.py:92`),
   `resources.retired_models` (`addons/angee/resources/entries.py:111`), `[[migrations]]`
   (`angee/conf.py:421`); see [Addon declarations](../../../docs/composer.md#addon-declarations).
   **Owner rule:** every addon fact lives here once; `AppConfig` carries no copy
   ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).

3. **Add `apps.py` only for native customization.** Notes has none. Add one for checks,
   signals or a pinned label (shape: `addons/angee/money/apps.py:17`):

   ```python
   class MoneyConfig(AppConfig):
       default = True
       name = "angee.money"            # must equal addon.name
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
   its namespace (`addons/angee/base/autoconfig.py:42`). Contribute only what the addon owns: `angee.base`
   already appends `PermissionIntegration` (`addons/angee/base/autoconfig.py:24`); a second registration is refused
   (`angee/compose/declarations.py:126`). **Owner rule:**
   [Autoconfig](../../../docs/composer.md#autoconfig) owns merge semantics; settings loading never imports models.

   <details><summary>Changes under the build/host plan</summary>Static `SETTINGS` dicts become hatch-angee build
   input, `settings(namespace)` callables become settings-module code, and model markers move into an `addon.toml` model section.</details>

5. **Register test models.** Framework addons add `testing/__init__.py` and `testing/models.py`: one
   concrete subclass per abstract source model (`abstract = False`, `app_label = "<label>"`, `db_table =
   "test_<label>_<model>"`, same `rebac_resource_type`); list the addon in `tests/settings.py` `INSTALLED_APPS`
   (`:29`) and import the module once in `tests/conftest.py` (`# noqa: F401`, `:645`); tests go in
   `tests/test_<name>*.py` (`angee-tests` owns their shapes). A consumer addon keeps `tests/` inside the addon (notes:
   `examples/addons/example/notes/tests/`; `pyproject.toml:205` puts `examples/addons` on the test path).
   **Owner rule:** [source-addon test models](../../../docs/checks.md#source-addon-test-models) own
   adoption; never register a second concrete model for a resource type.

6. **Register the web fragment** (only when the addon renders). Render `templates/addons/web` with `addon_id=<label>`,
   then adapt. `web/package.json`: name `@angee/<label>` (framework) or `@<ns>/<label>-web` (notes:
   `@angee-example/notes-web`), private, `exports: { ".": "./src/index.tsx" }`, `@angee/app` and `@angee/ui` as `workspace:*`.

   ```tsx
   // web/src/index.tsx — one greppable seam per addon (examples/addons/example/notes/web/src/index.tsx)
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
   `tsconfig.json` extends `addons/angee/tsconfig.base.json` by relative path. The template's `index.test.ts`
   calls `expectValidBaseAddon(addon)`; its `vitest.config.ts` passes `gqlAlias: []` until the fragment consumes
   `@angee/gql`. The stack's `pnpm-workspace.yaml` globs match `addons/angee/*/web`, stack-local `addons/*/*/web`
   and, under the full profile, `examples/addons/*/*/web`; a fragment in another repository slot needs one explicit
   line there. `WebRuntime` discovers the fragment from `web/package.json`; `[web]` is only for an explicit `package`
   or `codegen`. Routes, menus and pages are authored with `angee-pages` (`resourcePageRoutes`), never hand-written here.
   **Owner rule:** the fragment registers the addon's id and namespace once; page routing belongs to the
   pages skill's shared helper ([frontend rules](../../../docs/frontend/guidelines.md#rules)).

7. **Hand off host wiring.** `INSTALLED_APPS`, `settings.yaml`, the host `web/package.json` dependency and the stack profile belong to `angee-project`.

## Do not

- A manifest fact (`depends_on`, capability paths, a label that merely repeats the folder name) mirrored on `AppConfig`: the manifest owns it and the binding validates once (`angee/addons.py:19`); `label` belongs on `AppConfig` only to pin an already persisted label (`addons/angee/base/apps.py:22`) ([composition owners](../../../docs/backend/guidelines.md#composition-owners)).
- An addon-local registry, loader or settings parser for a fact `AutoConfig` or the manifest readers already read: the existing owner takes one explicit line instead ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).
- A second registration of a framework-owned integration such as `PermissionIntegration` when the base already appends it (`addons/angee/base/autoconfig.py:24`; duplicates refused at `angee/compose/declarations.py:126`).
- Model or `django.conf.settings` imports at `apps.py`/`autoconfig.py` module scope: settings merge runs before apps are ready, so only `ready()` and `settings(namespace)` may import ([Autoconfig](../../../docs/composer.md#autoconfig)).
- A module named for a noun (`statements.py`) when the concern already has an owning module (`models.py`, `managers.py`, a verb module) ([Naming](../../../docs/backend/guidelines.md#naming)).
- Customer, deployment or product names in framework-level code or fixtures; a consumer addon names its own product ([naming](../../../docs/guidelines.md#name-so-code-can-be-found-not-guessed)).
- Caveated REBAC subjects in `permissions.zed`: actor-scoped querysets have no caveat context, so `angee.E024` rejects them (`addons/angee/base/checks.py:37`); use field-backed conditional relations.
- An addon bundle under the `ui` i18n namespace or keys that override shared copy: the addon namespace equals its id and `ui` is reserved ([reserved namespace](../../../docs/frontend/guidelines.md#reserved-translation-namespace)).
- A manifest typed by annotation (`const x: BaseAddon = {…}`) instead of `defineBaseAddon`, which owns validation of the declared shape ([frontend rules](../../../docs/frontend/guidelines.md#rules)).

## Verify

Done means: `addon.toml` parses and its `name` equals the config `name` (`tests/test_addons.py:95`
`test_manifest_binding_validates_native_identity`); the bare host imports with the addon listed; the
fragment's `index.test.ts` passes; `runtime/web/manifest.json` (`addons/angee/web/build.py:149`) lists the package.

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
- A manifest fact mirrored on `AppConfig` drifts; the binding is validated once (`angee/addons.py:19`, [composition owners](../../../docs/backend/guidelines.md#composition-owners)).
- An invented list or marker loses to one explicit line in the existing owner ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).

## Read next

- [Addon declarations](../../../docs/composer.md#addon-declarations), [Addon discovery](../../../docs/composer.md#addon-discovery), [Glossary: Composition](../../../docs/glossary.md#composition)
- [Django-native rule](../../../docs/backend/guidelines.md#django-native-rule), [Package layering](../../../docs/backend/guidelines.md#package-layering), [Stack](../../../docs/stack.md)
- [Frontend package layering](../../../docs/frontend/guidelines.md#package-layering), [Ownership boundaries](../../../docs/frontend/guidelines.md#ownership-boundaries)
- Route: `angee-design` → `angee-addon` → `angee-models` + `angee-permissions` → `angee-graphql` + `angee-resources` → `angee-forms` + `angee-pages`, with `angee-tests` beside each artifact, then `angee-project`, `angee-runtime` and acceptance through `slice`.

## Sources

<!--
Docs: AGENTS.md; docs/guidelines.md; docs/backend/guidelines.md:27-138,2192-2244; docs/composer.md:137-196,494-540; docs/frontend/guidelines.md:15-127,187,1553,1762-1775; docs/checks.md:9-60,113-148,168-248,271; docs/glossary.md:8.
Code (grep -n on the working tree): installed hatch_angee manifest.py:33,189; angee/addons.py:19,73,79,91; angee/compose/autoconfig.py:29;
angee/compose/declarations.py:126; angee/conf.py:421; addons/angee/base/{autoconfig.py:23-24,42,apps.py:15-22,build.py:133,checks.py:16-37};
addons/angee/graphql/schema.py:656-665; addons/angee/mcp/server.py:92-101; addons/angee/resources/entries.py:111; addons/angee/web/build.py:77,101,115,149,242;
addons/angee/money/apps.py:17-30; addons/angee/tags/{addon.toml,testing/models.py,web/src/*}; examples/addons/example/notes/{addon.toml,autoconfig.py,README.md,web/*};
packages/app/src/{define-base-addon.ts:20,66,124,162,testing.tsx:101,architecture-guardrails.test.ts:231,774}; packages/app/config/vitest.ts:90;
packages/ui/src/i18n/index.ts:45; packages/app/bin/angee-web-codegen.mjs:60-110; tests/settings.py:29,71,144; tests/conftest.py:645; tests/test_addons.py:95;
pyproject.toml:205-208; templates/addons/web/template/**; templates/stacks/dev/template/pnpm-workspace.yaml.jinja.
Doc gaps: no addon.toml key reference in docs/; no Python addon template (templates/addons has only web/); no pnpm-workspace registration guide.
Mismatches: tags/apps.py and notes/apps.py do not exist (auto-created configs); notes/web ships no index.test.ts or vitest.config.ts (the template renders both).
-->
