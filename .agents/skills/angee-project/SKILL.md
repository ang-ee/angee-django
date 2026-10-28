---
name: angee-project
description: "Configures a customer system's host: stack root vs project vs workspace, angee.yaml profiles and include_* inputs, pyproject.toml, settings.yaml (INSTALLED_APPS, ANGEE_ADDON_DIRS, yamlconf, DATABASE_URL), secrets, first provision. Use for 'new project', 'enable addon', 'stack settings'."
---

# Angee Project

This skill owns host selection and settings: which addons a system composes and the files that
declare it. `angee-addon` owns an addon's local declarations; `angee-runtime` owns execution on a live stack.

## Use when

- "Set up a new project / customer system / stack for `<product>`."
- "Enable `arp.<x>` / `<ns>.<y>` in the system" or "add an addon repository."
- "Change `settings.yaml`, `INSTALLED_APPS`, `ANGEE_ADDON_DIRS`, `ANGEE_UI`."
- "Add a secret / set `DATABASE_URL`" or "why does the deployment ignore my setting?"

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `angee.yaml` + stack `AGENTS.md` | The stack: operator, sources, workspaces, jobs, services, secrets, ports; `ANGEE_ROOT` | `templates/stacks/_shared/{stack-body.yaml,AGENTS.md}.jinja` |
| `templates/{stacks/dev,stacks/local,projects/web}/copier.yml` | Stack inputs (`runtime_mode`, `addons_profile`, `include_arp`, `postgres_mode`, `serve_mode`, `framework`); project inputs (`project_name`, `addon_namespace`, `web_path`, `framework_workspace`, `framework_source_path`) | the three `copier.yml` files |
| `settings.yaml` | Root apps, addon roots, runtime and data dirs, dev defaults with `:jsonenv` markers | `templates/projects/web/template/settings.yaml.jinja` |
| `angee/conf.py` `ProjectContract` | `load()`: `.env`, project YAML/py, `YAMLCONF_CONFFILE`, defaults, service URLs (`_apply_defaults`); `compose()`: addon settings; generated `runtime/settings.py` body (`project_settings_source`) | `angee/conf.py:60,70,141,210,238,343,426` |
| `angee/defaults.py`, `angee/project.py`, `manage.py` | Core app prefix, `COMPOSE_ENV_PREFIXES=("ANGEE_",)`, SQLite floor; root discovery, `runtime.settings`; thin `angee.management.main()` entrypoint | `angee/defaults.py:46,57,71`, `angee/project.py:15,27,75`, `angee/management/__init__.py:17` |
| `angee/compose/autoconfig.py` | Addon settings merge; `COMPOSER_OWNED_SETTINGS` a project sets and an addon cannot | `angee/compose/autoconfig.py:26,29` |
| `pyproject.toml` + `angee/hatch.py` | Host requirements = hook config + selected addons' `addon.toml` deps (`RuntimeMetadataHook`); the build hook; editable framework source and `uv` cache keys (template) | `templates/projects/web/template/pyproject.toml.jinja`, `angee/hatch.py:24,63,318` |
| `pnpm-workspace.yaml`, `web/package.json` | Which `web/` folders the stack's JS install links; the host SPA and `codegen` script | `templates/stacks/dev/template/pnpm-workspace.yaml.jinja`, `templates/projects/web/template/{{ web_path }}/package.json.jinja` |

## Reuse first

- Render rather than hand-write: `angee init` chains `projects/web` under a stack template;
  the two `.copier-answers*.yml` files record every input, which is what a later
  `angee stack update --template` re-renders from ([templates](../../../templates/README.md#kinds)).
- `ProjectContract` and `angee.defaults` already map `DATABASE_URL`, `CACHE_URL`, `EMAIL_URL`,
  `ANGEE_DB_POOL*`, `ANGEE_PUBLIC_ORIGIN`, `SENTRY_DSN` ([stack rows](../../../docs/stack.md#backend)).
- django-yamlconf owns overlays, `YAMLCONF_<NAME>`, `:jsonenv`, `:append`, dotted keys and provenance
  ([Autoconfig](../../../docs/composer.md#autoconfig)); `AppGraph` expands `depends_on` ([App graph](../../../docs/composer.md#app-graph-and-settings)).
- The operator owns secrets (`angee secret set|reveal`), ports and the job graph;
  `AddonInstaller` (`addons/angee/platform/installer.py`) edits `INSTALLED_APPS` in place.

## Steps

1. **Find or create the stack root.** Walk up for `angee.yaml`
   ([resolve the root](../angee-workspace/SKILL.md#resolve-the-controlling-stack-root)).
   If one exists → it owns this checkout; configure there, because an `angee init`
   below it creates a second stack root for the same sources
   (`tests/test_agent_stack_root_policy.py:19`). If none and the user asked for a
   new system → render once:

   ```sh
   angee init <dir> -t dev --input addons_profile=base --input include_arp=true   # framework-dev
   angee stack init <template-url> <root> --input framework=source                # self-contained instance
   ```

   dev: `runtime_mode` process or docker, framework editable from `workspaces/src/angee`. local: docker
   only, `framework=source|baked` from `sources/angee`. Both ignore generated `runtime/` output and track
   `runtime/<label>/migrations/` (`templates/stacks/dev/template/.gitignore.jinja:11`,
   `templates/stacks/local/template/.gitignore.jinja:8`): output is rebuilt, history is authored and committed.
   **Owner rule:** the stack template owns layout and profile facts; the chained project template owns host files ([layouts](../../../templates/README.md#stacks-framework-dev-vs-local-instance)).

2. **Keep `pyproject.toml` as rendered.** Host requirements live once in the hook
   table; the metadata hook adds each selected addon's `addon.toml` `dependencies`.

   ```toml
   [project]
   dynamic = ["dependencies", "entry-points"]
   [tool.hatch.metadata.hooks.angee-runtime]
   dependencies = ["django-angee[postgres]>=0.3"]      # host-authored requirements only
   [tool.hatch.build.hooks.angee-runtime]
   require-runtime-dependencies = true
   [tool.uv.sources]
   django-angee = { path = "workspaces/src/angee", editable = true }   # framework_source_path
   [tool.uv]
   cache-keys = [{ file = "settings.yaml" }, { file = "addons/**/addon.toml" }, …]
   ```

   An addon's library → its `addon.toml` (`angee-addon`); listed here it is a second copy of what the hook
   already projects. An extra addon root → its `cache-keys` entries. **Owner rule:** `addon.toml` owns
   addon requirements; the hook projects them ([composed dependencies](../../../docs/composer.md#composed-addon-dependencies)).

   <details><summary>Changes under the build/host plan</summary>
   A host whose <code>pyproject.toml</code> carries a <code># GENERATED by angee build</code>
   <code>[dependency-groups] addons</code> block predates the Hatch hook: re-render it from the project
   template instead of editing the block. Under the plan the build moves into hatch-angee without
   Django, static autoconfig dicts become build input, and <code>angee.base</code> owns the host.
   </details>

3. **Declare roots and structural paths in `settings.yaml`.** These are build
   inputs: `ProjectContract.load` and the Hatch hook read them without Django.

   ```yaml
   INSTALLED_APPS:            # roots in precedence order; dependencies expand
     - angee.iam
     - angee.platform
     - angee.operator
     - <addon_namespace>.<app>          # consumer addons under addons/<addon_namespace>/
     - arp.<app>                        # arpee, only with include_arp
   ANGEE_ADDON_DIRS:          # importable roots, first match wins; NOT a second app list
     - "{BASE_DIR}/addons"
     - "{BASE_DIR}/workspaces/src/angee/addons"
     - "{BASE_DIR}/workspaces/src/angee-arp/addons"
   COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"
   ANGEE_DATA_DIR: "{BASE_DIR}/data"
   ```

   The host prefixes `django_yamlconf`, `angee.compose`, `angee`, `angee.base`; do not list them. Another
   addon repository → one stack `sources:` entry, one `ANGEE_ADDON_DIRS` line, its roots here, one pnpm glob
   (step 6). **Owner rule:** `INSTALLED_APPS` is the one root-addon contract; `ANGEE_ADDON_DIRS` only makes
   roots importable ([Settings bootstrap](../../../docs/composer.md#settings-bootstrap)).

4. **Keep deployment values late-bound.** `settings.yaml` holds dev defaults; the
   deployment overrides each through the environment — a second settings file
   would be a second owner of the same value.

   ```yaml
   SECRET_KEY: <project_name>-dev-key     # YAMLCONF_SECRET_KEY replaces it (stack: ${secret.secret-key})
   SESSION_COOKIE_NAME: <project_name>_sessionid   # and CSRF_COOKIE_NAME: localhost stacks share cookies
   DEBUG: true
   DEBUG:jsonenv: true                     # YAMLCONF_DEBUG is JSON-decoded; without the marker it stays a string
   ANGEE_UI:                               # deployment shell layer; keys validated by addons/angee/web/build.py
     shell: { brand: { name: <Brand> }, hosts: { app.example.test: <app> } }
   ```

   `DATABASE_URL`, `CACHE_URL`, `EMAIL_URL` come from the process environment (stack `env:`; a
   project-root `.env` serves host-run `manage.py`, process env winning); an explicit `DATABASES` wins
   over the URL. A name an addon declares with the `ANGEE_` prefix is read from the environment as-is;
   any other needs `YAMLCONF_`. `YAMLCONF_CONFFILE` adds a whole overlay (the baked image uses it for
   `ANGEE_ADDON_DIRS:append`). A secret → stack `secrets:` and `${secret.<name>}`. **Owner rule:**
   `ProjectContract` owns the precedence project YAML → overlay → environment → addon defaults ([django-environ row](../../../docs/stack.md#backend), `angee/conf.py:210,238,240`).

5. **Leave `manage.py` and the generated settings alone.** `manage.py` only calls `angee.management.main()`;
   `project.bootstrap` finds the root (`ANGEE_PROJECT_DIR`, the `manage.py` directory, then ancestors with
   `settings.yaml`) and selects `runtime.settings`, whose generated body calls `ProjectContract.compose`. A
   `settings.py` beside `manage.py` is optional (`ANGEE_PROJECT_SETTINGS`); YAML layers over it.
   **Owner rule:** the host owns boot wiring ([Serving](../../../docs/composer.md#serving)).

6. **Register JS packages at the stack root.** `pnpm-workspace.yaml` lists the host
   package, local consumer fragments and every slot's fragments; the stack's `deps` job
   runs `pnpm install` there.

   ```yaml
   packages:
     - "web"
     - "addons/*/*/web"
     - "workspaces/src/angee/packages/*"
     - "workspaces/src/angee/addons/angee/*/web"
     - "workspaces/src/angee-arp/addons/arp/*/web"     # include_arp
   ```

   The host `web/package.json` keeps `@angee/app`, `@angee/ui`, `@angee/metadata`, `@angee/refine` as
   `workspace:*` plus the `codegen` script; generated `runtime/web/app` imports fragments by path from
   `runtime/web/manifest.json`, so a fragment needs a glob here, not a host dependency. **Owner rule:**
   the stack root owns the JS install; an install inside a slot forks the dependency identity of every
   linked package ([working directory](../../../docs/checks.md#choose-the-working-directory)).

7. **Provision through the operator.** `angee dev` (or `angee up`) materializes sources, cuts
   `workspaces/src`, then runs the stack's declared job graph
   (`templates/stacks/_shared/stack-body.yaml.jinja:255,262,285,325`). The build (editable or
   `--no-editable` by serve mode), migration authoring, provision, where codegen runs (the `codegen`
   job on framework stacks, inside `frontend-build` on instance stacks) and the restart job are
   `angee-runtime` steps 2–7; after a settings or roster change on a running stack, run that skill's
   step 7. Which sync shape a live stack needs is `angee-permissions` step 7's decision, not this skill's.
   **Owner rule:** the job graph owns ordering; `angee provision` owns the step list ([restart](../../../docs/howto/getstarted.md#restart-the-running-stack), `angee/management/provision.py:49`).

## Do not

- Run `angee init` or create `.angee/` under an existing stack or a source checkout ([Set it up](../../../docs/howto/getstarted.md#set-it-up), `tests/test_agent_stack_root_policy.py:19`).
- `pnpm install` or bare `pnpm exec` inside a slot; install at the stack root ([working directory](../../../docs/checks.md#choose-the-working-directory)).
- Edit `runtime/` or a generated block; rebuild from source ([Invariants](../../../docs/composer.md#invariants)).
- Add a second addon list, env-driven discovery or a project-local settings loader beside `INSTALLED_APPS` + `ProjectContract` ([Settings bootstrap](../../../docs/composer.md#settings-bootstrap)).
- Set `INSTALLED_APPS`, `COMPOSE_RUNTIME_DIR` or `COMPOSE_RUNTIME_MODULE` from an addon `autoconfig.py` (`angee/compose/autoconfig.py:26`).
- Put secrets, machine paths or `.env` contents into `settings.yaml`; use `{BASE_DIR}`, `${secret.*}` and env ([composed dependencies](../../../docs/composer.md#composed-addon-dependencies)).
- Start Django, Vite or workers by hand, or `uv sync` the venv while the stack runs ([Run from the root](../../../AGENTS.md#run-from-the-root), [restart](../../../docs/howto/getstarted.md#restart-the-running-stack)).
- Choose a `rebac sync` shape (plain, `--prune-stale`, `--force-overwrite`) from a settings or roster change → `angee-permissions` step 7 owns the decision; provision schedules it (`angee/management/provision.py:58-60`, [REBAC](../../../docs/backend/guidelines.md#rebac) lines 825-830).
- Hand-parse `.env` for a secret; `angee secret reveal` reads the backend ([run a local stack](../../../templates/README.md#run-a-local-stack)).
- Declare caveated REBAC subjects anywhere in the system; `angee.E024` rejects them (`addons/angee/base/checks.py:37`), use field-backed conditional relations.

## Verify

Done means: `uv sync` from the stack root emits `runtime/` and `runtime/web/manifest.json` lists every
expected fragment; `angee provision` prints `angee provision: ok`; `angee --root "$ANGEE_ROOT" status --json`
shows every service running and none completed; `check` and `schema --check` pass after codegen.

- In a lane (jj workspace: no host, database or JS install): `tests/test_stack_templates.py` (`:921,974,1137`),
  `tests/test_project_template_inputs.py`, `tests/test_settings.py` (`:656,871,947`), `tests/test_hatch.py`,
  `tests/test_agent_stack_root_policy.py`, ruff on touched files — [Python contracts](../../../docs/checks.md#python-and-composition-contracts).
- Only on the integrated stack: `uv sync`, `angee check`, `angee provision`, `manage.py check`,
  `schema --check`, codegen, composed typecheck, browser — [composition](../../../docs/checks.md#composition-and-schema),
  [frontend](../../../docs/checks.md#frontend-packages-and-addon-fragments). Loop: `.agents/skills/slice/SKILL.md`.
- False greens: `angee stack update --template` merges and keeps keys the template stopped emitting
  ([shared servers](../../../templates/README.md#connect-framework-dev-stacks-to-shared-postgresql-and-redis-servers));
  a green chained restart has synced the zed (`angee/management/provision.py:58`) but has not authored
  missing migrations — provision refuses drift (`:62`; `angee-runtime` step 3).

## Pitfalls

- An ancestor `settings.yaml` does not contribute to a nested project; the project file and `YAMLCONF_CONFFILE` load (`tests/test_settings.py:947`, [Settings bootstrap](../../../docs/composer.md#settings-bootstrap)).
- `YAMLCONF_<NAME>` stays a string unless the YAML declares `<NAME>:jsonenv: true` (`tests/test_settings.py:871`).
- A `DATABASE_URL` carrying `CONN_MAX_AGE` fails once `ANGEE_DB_POOL` is on; only production Django workers pool (`angee/conf.py:366`, [serve mode](../../../docs/stack.md#stack-serve-mode)).
- Two localhost stacks with the same cookie names log each other out (`tests/test_stack_templates.py:891`).
- The first `ANGEE_ADDON_DIRS` root that declares an addon name wins; a shadowing root silently changes requirements (`angee/hatch.py:57-58`, `tests/test_hatch.py:37`).
- A `manage.py` that does not call `angee.management.main()` fails its first captured write; re-render it (`tests/test_stack_templates.py:1112`).

## Read next

- [Composer: Flow](../../../docs/composer.md#flow), [Example project shape](../../../docs/composer.md#example-project-shape), [Autoconfig](../../../docs/composer.md#autoconfig)
- [Stack serve mode](../../../docs/stack.md#stack-serve-mode), [Get started](../../../docs/howto/getstarted.md#set-it-up), [Templates](../../../templates/README.md#stacks-framework-dev-vs-local-instance), [Glossary: Composition](../../../docs/glossary.md#composition)
- Skills: `angee-design` (first), `angee-addon`, `angee-runtime`, `angee-workspace`, `slice`

## Sources

<!--
Docs: AGENTS.md; stack-root + workspace AGENTS.md (read-only); docs/guidelines.md; docs/composer.md:20-44,83-200,448-487,534-600;
docs/stack.md:10-30,82-90,159-220,335-345; docs/howto/getstarted.md; docs/checks.md:9-60,168-206; docs/backend/guidelines.md:82-100,395-410,805-830,2170-2180;
docs/glossary.md:8-75,455-465; templates/README.md. Templates: templates/projects/web/**; templates/stacks/{dev,local,_shared}/**; templates/workspaces/src/copier.yml;
docker/addon-settings.yaml. Code: angee/{conf,project,defaults,hatch,apps}.py; angee/management/{__init__,provision}.py + commands/angee.py; angee/compose/
{autoconfig,yamlconf,apps}.py; addons/angee/web/build.py; addons/angee/platform/{installer,autoconfig}.py; tests/test_stack_templates.py:621-1145; tests/test_hatch.py:1-60;
tests/test_settings.py:640-1000; angee/management/provision.py:49-67. Shapes checked against a stack rendered from templates/stacks/dev (angee.yaml, settings.yaml,
pyproject.toml, manage.py, pnpm-workspace.yaml, web/package.json, .copier-answers*.yml, runtime/web/manifest.json).
Outstanding discrepancies: templates/projects/web/template/settings.yaml.jinja:5 cites docs/backend/guidelines.md for the `:jsonenv` marker, which no file under
docs/ documents (owner: django-yamlconf; tests/test_settings.py:871). docs/glossary.md defines Project (:48) and Host (:62) but no Stack or Workspace term.
-->
