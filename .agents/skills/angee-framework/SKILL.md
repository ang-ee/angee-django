---
name: angee-framework
description: "Routes a change to a framework seam instead of an addon: core/angee/ (conf, defaults, addons loader, asgi, management, hooks, compose), the angee.base foundation, @angee/* package layering, the Copier template chain, docs reconciliation and the dependency policy. Names each seam's owner files, layering validators and required checks."
---

# Angee Framework

The [Area Rules](../../../AGENTS.md#area-rules) own what each area may contain; this skill is their
procedure: it routes to the seam, its owner files and its validators, and links the contracts instead of
restating them. Run `angee-design` first for the owner map; `angee-addon`, `angee-models` and `angee-permissions` own addon-level work.

## Use when

- "This belongs in core / in `angee.base` / in `@angee/ui`, not in the addon."
- "Add a host seam, hook setting, serving entrypoint or management command to `core/angee/`."
- "Add a foundation mixin, field, queryset primitive or system check to `angee.base`."
- "Add or move a shared React owner; a package import breaks the layering test."
- "Change a template, a dependency (Python or JS), or a doc that disagrees with code."

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `core/angee/{conf,defaults,addons,hooks,asgi,urls,management}` | host settings contract, ordered core apps, native package metadata, conventional contributions, hook settings, stable serving entrypoints, `manage.py` entry | `core/angee/conf.py:60`, `core/angee/defaults.py:46`, `core/angee/addons.py:111`, `core/angee/hooks.py:47`, `core/angee/asgi.py:20`, `core/angee/management/__init__.py:17` |
| `core/angee/compose/`, `core/angee/build.py` | build-time composition and explicit fresh-process runtime generation | `core/angee/compose/apps.py:75`, `core/angee/compose/declarations.py:78`, `core/angee/build.py` |
| `addons/angee/base/` (label `base`) | foundation models, fields, querysets, managers, permissions, checks; `build.py` is the one base module that may import the compiler | `addons/angee/base/apps.py:15`, `checks.py:16`, `models/trash.py:90`, `querysets.py:184`, `build.py:460` |
| `packages/{refine,metadata,ui,app,e2e,storybook}` | schema-independent `@angee/*`; `packages/tsconfig.base.json` and `packages/vitest.shared.ts` are the shared config | `packages/app/src/architecture-guardrails.test.ts:29` |
| `templates/{projects,addons,stacks,workspaces,services}` | Copier sources; each `copier.yml` `_angee` block is the contract; stacks chain `projects/web` | `templates/stacks/dev/copier.yml:45,129`, [kinds](../../../templates/README.md#kinds) |
| `pyproject.toml`, `<addon>/pyproject.toml`, `package.json`, `uv.lock`, `pnpm-lock.yaml`, `docs/stack.md` | one dependency fact in three places: concern row, owning manifest, lock | `pyproject.toml:34`, `addons/angee/base/pyproject.toml:7`, [stack](../../../docs/stack.md#how-the-stack-is-locked) |
| `tests/test_base_layering.py`, `tests/test_layering.py` | dependency direction and import closures | `tests/test_base_layering.py:135,209`, `tests/test_layering.py:70,212` |
| `tests/test_documentation_contracts.py`, `tests/test_*template*.py` | links, anchors and documented recipes; rendered template contracts | `tests/test_documentation_contracts.py:13`, `tests/test_stack_templates.py:626` |
| `AGENTS.md` Read By Task, `docs/*.md` | which guide owns which rule; code owns exact contracts | [Read By Task](../../../AGENTS.md#read-by-task) |

## Reuse first

- The seam's native extension point: `addon_contribution` / `optional_addon_module` (`core/angee/addons.py:79,111`)
  for conventional modules; `resolve_hook` + `check_hooks` (`core/angee/hooks.py:47,71`) for a callable setting;
  `BuildIntegration` / `AppDeclarationSource` (`core/angee/compose/declarations.py:23,78`) for build contributions;
  [autoconfig](../../../docs/composer.md#autoconfig) for settings fragments.
- A row of the [owner map](../../../docs/composer.md#owner-map) or [composition owners](../../../docs/backend/guidelines.md#composition-owners): extend that row.
- Base rosters: `addons/angee/base/models/*.py` (one mixin per module), `fields/__init__.py`, `querysets.py`
  (`CanonicalQuerySet:103`, `AngeeQuerySet:184`), `managers.py:13`; see [persistence primitives](../../../docs/backend/guidelines.md#persistence-primitives).
- The React [package layering](../../../docs/frontend/guidelines.md#package-layering) table; `@angee/ui` owners
  and `@angee/app/testing` harnesses before a local component or provider setup.
- The [stack](../../../docs/stack.md#backend) library that owns the concern, its native API first.
- Nearby seams of the same shape: `angee.mcp.asgi` (HTTP mount), `angee.graphql` (`asgi.py`, `schema.py`),
  `angee.resources.build` / `angee.web.build` (build integrations); template source, never a rendered copy.

## Steps

1. **Fix the level.** Consumer vocabulary → an addon (`angee-addon`). Reusable Django row behavior →
   `angee.base`. Host composition, serving or lifecycle with no model → `core/angee/`. A rendered surface every
   addon shares → the lowest `@angee/*` package the DAG allows. A scaffold → `templates/`. Record the owner
   map and deletion list. **Owner rule:** [Repository Role](../../../AGENTS.md#repository-role) decides; core
   owns no product models and never names addon vocabulary ([Area Rules](../../../AGENTS.md#area-rules)).

2. **Core seam: extend a declared contract.** Serving code enumerates Django's app registry and reads one
   conventional module or setting; it never imports `angee.compose` or a folder addon. A capability that
   needs a callable declares a setting and its `AppConfig` checks it:

   ```python
   # addons/angee/base/apps.py:12,49 — the owner of ANGEE_MERGE_GUARDS checks it
   check_settings = partial(check_hooks, ("ANGEE_MERGE_GUARDS",))
   ...
   checks.register(check_settings, checks.Tags.models)   # in BaseConfig.ready()
   ```

   Routes and sockets are conventional `urls.py` / `asgi.py` read by `core/angee/urls.py:9` and `core/angee/asgi.py:20`
   ([serving](../../../docs/composer.md#serving)). A build contribution subclasses `BuildIntegration` (as
   `addons/angee/base/build.py:460`), selected by `COMPOSE_INTEGRATIONS:append`, never imported by serving
   modules. A project fact → `ProjectContract` / `angee.defaults` ([settings bootstrap](../../../docs/composer.md#settings-bootstrap));
   a lifecycle step → `plan()` (`core/angee/management/provision.py:49`) and the `angee` command's `check` /
   `clean` / `provision` (`core/angee/management/commands/angee.py:29`).
   **Owner rule:** [Django-native rule](../../../docs/backend/guidelines.md#django-native-rule): core owns only
   the composition seam; when Django already owns the object, a core wrapper over it is a redundant wrapper
   the [constitution](../../../AGENTS.md#constitution) removes.

   

3. **Base seam: a mixin, field, queryset primitive or check.** One capability per module under
   `addons/angee/base/models/`; the mixin is abstract and owns its check:

   ```python
   # addons/angee/base/models/trash.py:90,149,163 — shape of a foundation mixin
   class TrashMixin(PersistenceMixin, models.Model):
       class Meta:
           abstract = True

       @classmethod
       def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
           errors = super().check(**kwargs)
           ...  # checks.Error(..., obj=cls, id="angee.E036")
           return errors
   ```

   A registry-wide check is a function in `checks.py` registered in `BaseConfig.ready()` (`addons/angee/base/apps.py:49-52`)
   with the next unused `angee.E0nn` id. A field joins `fields/__init__.py`; a queryset extends
   `CanonicalQuerySet`, and its manager binds `Manager.from_queryset` at module level ([stack](../../../docs/backend/guidelines.md#stack)).
   Base imports nothing from `angee.compose` or another addon (`tests/test_base_layering.py:124`); only
   `build.py` and `typing.py` may import the compiler (`tests/test_layering.py:212`).
   **Owner rule:** [persistence primitives](../../../docs/backend/guidelines.md#persistence-primitives): the row
   contract and its check live on the mixin; callers compose, never re-guard.

4. **Packages seam.** Put the owner in the lowest package `FRAMEWORK_IMPORT_RULES` (`architecture-guardrails.test.ts:29`)
   permits: `refine` and `metadata` import no sibling, `ui` reads `AppRuntime` from its own context, `app`
   composes. Export through the package's `package.json` `exports` map, extend `../tsconfig.base.json`, take
   Vitest config from `packages/vitest.shared.ts`. A new shared owner needs a production consumer (`architecture-guardrails.test.ts:476`)
   and is never a deleted shell (`DELETED_SHELLS`, `:17`). A UX rule becomes a `ux-ontology-guard.ts` rule, not an allowance.
   **Owner rule:** [one-way rules](../../../docs/frontend/guidelines.md#one-way-rules), [ownership boundaries](../../../docs/frontend/guidelines.md#ownership-boundaries);
   packages never import `@angee/gql/*` ([Area Rules](../../../AGENTS.md#area-rules)).

5. **Templates seam.** Edit `templates/<kind>/<name>/copier.yml` and `template/*.jinja`; keep the `_angee`
   block current, declare an input with `type`/`default`/`help`, pass it through the chain
   (`stacks/*/copier.yml:129` `chain: [{template: ../../projects/web}]`) and write includes as `../_shared/<file>`
   ([stacks](../../../templates/README.md#stacks-framework-dev-vs-local-instance)). Pin the rendered value in the
   owning test (`tests/test_stack_templates.py`, `test_workspace_templates.py`, `test_project_template_inputs.py`,
   `test_service_templates.py`). **Owner rule:** `_angee.description` is the template's contract; a rendered
   `AGENTS.md.jinja` describes the target, not the template source ([Area Rules](../../../AGENTS.md#area-rules)).

6. **Dependency change.** Change the concern row in `docs/stack.md`, the owning manifest (`pyproject.toml:34`
   `core/pyproject.toml` core, `<addon>/pyproject.toml` `[project].dependencies`, `package.json`) and the lock regenerated at the owning root
   together; a distribution declares its own native requirements with compatible addon ranges (`tests/test_base_addon_dependencies.py:34`).
   If the library owns the concern → wire it; if the row is under Proposed → move it first.
   **Owner rule:** [change policy](../../../docs/stack.md#change-policy), [how the stack is locked](../../../docs/stack.md#how-the-stack-is-locked).

7. **Compatibility for a required import.** Keep only an alias a fingerprinted migration or native command
   precedence still needs, beside the canonical owner — the shape is `core/angee/management/commands/createsuperuser.py:11-17`
   (bind `Command` to the IAM owner when installed, else Django's; no logic). Register the module in
   `CORE_ADDON_BINDINGS` (`tests/test_base_layering.py:22`) so the core-to-addon import is allowed only there;
   retire it with its last consumer. **Owner rule:** [Constitution](../../../AGENTS.md#constitution): a narrow
   alias to the canonical implementation, never a competing active implementation.

8. **Guard the seam.** A new or changed boundary gets a focused test beside its peers: direction in
   `tests/test_base_layering.py`, closure in `tests/test_layering.py`, settings/app graph in `tests/test_settings.py`,
   emission in `tests/test_compose.py`, native package metadata in `tests/test_addons.py`, package layering in the
   guardrails test. **Owner rule:** [seam guards](../angee-tests/references/environment.md#seam-guards): test boundaries, not prescribed words.

9. **Reconcile docs.** Update the owning guide's rule and link to the code; move exact shapes into docstrings;
   delete superseded guidance in the same change. Prose and code disagree → [reconcile](../../../docs/guidelines.md#reconcile-code-docs-and-tests)
   toward the verified owner, or escalate an undecided convention. **Owner rule:**
   [docs teach intent; code explains contracts](../../../docs/guidelines.md#let-code-carry-code-contracts).

## Do not

- Import a folder addon from `core/angee/` outside `CORE_ADDON_BINDINGS`: core depends on no addon (`tests/test_base_layering.py:135`, [package layering](../../../docs/backend/guidelines.md#package-layering)).
- Import `angee.compose` or a `build.py` from a serving module (`tests/test_layering.py:212`, [package layering](../../../docs/backend/guidelines.md#package-layering)).
- Subclass a shared Angee `AppConfig` in an addon (`tests/test_base_layering.py:179`, [Django-native rule](../../../docs/backend/guidelines.md#django-native-rule)).
- Add a registry, loader or merge language beside `AutoConfig`, the package readers or `angee.hooks` ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).
- Let `@angee/refine` import a sibling, `@angee/ui` import `@angee/app`, or any package import `@angee/gql/*` ([one-way rules](../../../docs/frontend/guidelines.md#one-way-rules)).
- Add a dependency, including a boundary linter, without its stack row and lock ([change policy](../../../docs/stack.md#change-policy)).
- Hand-edit `runtime/` or rendered template output ([invariants](../../../docs/composer.md#invariants)).
- Put product vocabulary or customer names in framework code or fixtures ([naming](../../../docs/guidelines.md#name-so-code-can-be-found-not-guessed)).
- Declare caveated REBAC subjects in a base schema: `angee.E024` (`addons/angee/base/checks.py:37`); use field-backed conditional relations.
- Run `rebac sync --force-overwrite` as a routine step; provision runs `--prune-stale` and overwrite is a separate explicit choice ([REBAC](../../../docs/backend/guidelines.md#rebac), `core/angee/management/provision.py:58-60`).

## Verify

Done means: the layering and guardrails tests pass unchanged or with the guard you added; the full SQLite
suite passes for any `core/angee/` change; a template change pins its rendered value; a doc change resolves every link and recipe.

- In a lane (jj workspace: no host, database or JS install): focused pytest, ruff and mypy on touched files,
  `tests/test_base_layering.py`, `tests/test_layering.py`, `tests/test_compose_imports.py`, `tests/test_hatch.py`, the
  template and documentation contract tests — [Python contracts](../../../docs/checks.md#python-and-composition-contracts),
  [methodology and documentation](../../../docs/checks.md#agent-methodology-and-documentation).
- Only on integrated `src`: the full suite for core, `angee check`, `check`, `schema --check`, codegen, per-package
  typecheck/test/build, distribution and export checks, the guardrails test, Storybook catalog —
  [composition](../../../docs/checks.md#composition-and-schema), [frontend](../../../docs/checks.md#frontend-packages-and-addon-fragments).
- Loop: `.agents/skills/slice/SKILL.md`. False greens: the composed `web` typecheck skips `*.test.tsx`; `@angee/gql`
  is stale until codegen reruns; browser e2e and vulture are outside CI ([what CI runs](../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- Settings loading that imports a model or `django.conf.settings` at module scope breaks phase ordering ([autoconfig](../../../docs/composer.md#autoconfig), `tests/test_compose_imports.py:14`).
- A manager built by calling `Manager.from_queryset(...)` inline as a class base loses django-stubs typing ([backend stack](../../../docs/backend/guidelines.md#stack)).
- A bare `pnpm exec` in a slot installs a private store and forks package identities ([checks](../../../docs/checks.md#choose-the-working-directory)).
- A new list, env var, marker or inference where one explicit line in an existing owner file would do is rejected ([Constitution](../../../AGENTS.md#constitution), "Less code is more").
- `angee.E033` fires for an unimportable or multi-valued single hook; declare the setting on its owner and check it there (`core/angee/hooks.py:47,67`).
- Test-support packages (`*.testing`, `tests`) imported from production code fail layering (`tests/test_base_layering.py:156`).

## Read next

- [Composer flow](../../../docs/composer.md#flow), [owner map](../../../docs/composer.md#owner-map), [invariants](../../../docs/composer.md#invariants)
- [Package layering](../../../docs/backend/guidelines.md#package-layering), [framework contracts](../../../docs/backend/guidelines.md#framework-contracts), [naming](../../../docs/backend/guidelines.md#naming)
- [Frontend package layering](../../../docs/frontend/guidelines.md#package-layering), [native state owners](../../../docs/frontend/guidelines.md#native-state-owners), [stack tooling](../../../docs/stack.md#tooling), [templates](../../../templates/README.md#kinds), [agent methodology](../../README.md)
- [Web pitfalls](references/web-pitfalls.md): build, codegen, install and package-layering traps moved from the frontend guide
- Skills: `angee-design` (first), `angee-addon`, `angee-models`, `angee-permissions`, `angee-workspace`, `slice`

## Sources

<!--
Docs: AGENTS.md; docs/guidelines.md; docs/backend/guidelines.md #stack, #django-native-rule, #package-layering, #composition-owners, #rebac, #workflow-contracts, #naming, #checks; docs/frontend/guidelines.md #stack, #package-layering; docs/composer.md #flow, #owner-map, #settings-bootstrap, #composed-addon-dependencies, #runtime-cleanup, #addon-declarations, #serving, #invariants; docs/stack.md #how-the-stack-is-locked, #backend, #rendered-binding, #tooling, #proposed-not-locked, #change-policy; docs/checks.md (whole); templates/README.md; .agents/README.md.
Code: core/angee/*.py docstrings; core/angee/{defaults,hooks,apps,autoconfig}.py; core/angee/{addons,hatch,asgi,urls}.py (defs); core/angee/conf.py:360-395; core/angee/compose/apps.py; core/angee/compose/declarations.py (defs); core/angee/management/{__init__,provision}.py; core/angee/management/commands/{angee,createsuperuser}.py; addons/angee/base/{pyproject.toml,apps.py,checks.py,fields/__init__.py,models/__init__.py}; addons/angee/base/{build,autoconfig,querysets,managers,models/trash,models/ownership}.py (defs); tests/test_layering.py:1-100,165-260; tests/test_base_layering.py (whole); tests/test_documentation_contracts.py:1-60; def lists of tests/test_{base_checks,compose_imports,hatch,defaults,management,provision,base_addon_dependencies,addons}.py and the five template tests; packages/app/src/architecture-guardrails.test.ts:1-80 and test names; packages/{tsconfig.base.json,vitest.shared.ts,app/package.json}; pnpm-workspace.yaml; pyproject.toml:1-60,156-190; templates/{projects/web,addons/web,stacks/dev,stacks/local}/copier.yml heads.
Outstanding discrepancies: tests/test_base_layering.py:22-30 (`CORE_ADDON_BINDINGS`) allowlists core/angee/fields.py, core/angee/impl.py, core/angee/indexes.py and core/angee/models/*, while core/angee/ contains only the two IAM command shims (core/angee/management/commands/{createsuperuser,changepassword}.py).
Doc gap: no doc lists the per-seam validators or the compatibility-alias registration (derived from tests/test_base_layering.py).
-->
