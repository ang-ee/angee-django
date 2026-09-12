# AGENTS.md

Angee is a thin composition framework for Django + React applications. The
framework owns the seams; addons own product capabilities. The composer turns
addon contracts and project settings into a runnable project. Projects declare
root apps through Django `INSTALLED_APPS`.

## Constitution

These are binding engineering rules for every change. Task size changes the
amount of process, never the standard. Area guides and agent workflows apply
these rules; they do not relax them. Framework defects multiply through addons,
projects, examples, tests, and future designs, so the foundation demands the
highest standard.

- **Find the owner first.** Every fact and behavior has an owning level: an
  upstream library, framework primitive, model, field, manager, queryset,
  manifest, or addon. Read that owner before writing a caller. Never re-derive,
  re-decode, or re-decide from outside what it already knows. Put behavior on the
  object that owns the data; repair or extend an insufficient owner there.
- **DRY: one fact, one rule, one implementation.** A reusable capability lives
  once at its owning level; all consumers compose it. When the same rule appears
  twice, find the owner, consolidate callers, and delete the copy. This applies
  to code, state, schemas, configuration, and documentation. Different intent
  must remain distinct; superficial similarity does not justify an abstraction.
  The [DRY guide](docs/guidelines.md#dont-repeat-yourself-dry) explains the test.
- **Do not reinvent the wheel. Lean hard on upstream libraries.** Consult
  [the stack](docs/stack.md), the locked dependency's native API, and existing
  usage before implementing a concern. If a library owns it, wire that library;
  do not rebuild it. Use its native types, state, lifecycle, and extension points.
  Angee owns the missing composition seam and keeps that glue thin.
- **Compose reusable components; never reimplement them locally.** Search the
  framework's views, forms, navigation, glyphs, state surfaces, and backend
  primitives before creating one. A gap requires fixing or extending the shared
  owner so every consumer inherits the result. Copies and hand-rolled substitutes
  silently lose shared behavior and fixes. Addons and pages declare intent and
  compose owners; they do not carry private versions of framework capabilities.
- **No parallel implementations alongside an existing owner.** Do not add a
  second helper, service, cache, registry, parser, state machine, or configuration
  surface for a concern already owned by the framework or a dependency. A new
  name or wrapper does not make duplication legitimate. Extend the owner through
  its contract. When replacing an implementation, migrate its callers and remove
  the superseded path and guidance; leaving both behind is unfinished work.
  Preserve required historical imports through narrow compatibility aliases to
  the canonical implementation, and preserve released migration bodies under the
  [migration policy](docs/backend/guidelines.md#migrations-and-runtime). These
  required contracts do not authorize a competing active implementation.
- **Less code is more. Prefer deletion to abstraction.** Remove dead paths,
  redundant wrappers, speculative options, and unnecessary indirection before
  adding machinery. A new abstraction must remove real duplication or own a
  demonstrated required capability that no existing owner or native extension
  can provide. Use the smallest shape that makes the next change smaller and
  clearer; speculative abstraction is forbidden. Reduce total maintained code
  across owners and callers while preserving required behavior, clarity, and
  useful checks.
- **Kill rot and technical debt whenever you encounter it.** Fix the cause at
  its owner, migrate callers, and delete obsolete code, stale instructions, and
  workarounds. Existing debt is a defect to remove, never a pattern to copy or an
  excuse to add more. If removal requires a broader migration or exceeds the
  user's authorization, report the exact debt and required fix explicitly; do
  not hide it or claim the affected work is complete.
- **Never quick-fix merely to make something run.** Invest in the correct owner
  even when that fix is harder. Do not cut agreed features, weaken contracts or
  checks, or add consumer workarounds to hide build, schema, test, or dev failures.
  A green check does not excuse an architectural defect.
- **Dependencies point toward stable owners.** Framework and base addons never
  depend on consumers or examples; serving code never imports the build-time
  composer. Commands, resolvers, routes, and adapters dispatch to policy owners.
- **Compose through declared contracts.** Addon declaration facts belong to
  `addon.toml`; Django `AppConfig` owns app identity and lifecycle. Use schema
  buckets, model `extends`, input/type extensions, settings/autoconfig,
  `ImplClassField`, slots, registered forms/glyphs, generated SDL, and native
  dependency extension points. Never probe foreign object shapes or monkey-patch
  composed model classes. The [owner decision tree](docs/guidelines.md#put-behavior-on-the-owning-object)
  distinguishes owner behavior from legitimate cross-owner orchestration.
- **Composition belongs to the composer.** Source declarations and the compiler
  own generated output. Do not hand-edit artifacts or add addon-local runtime
  registries. Native Django app population/signals and composer-owned bootstrap
  remain valid lifecycle mechanisms; see [composition phases](docs/composer.md#flow).
- **Keep domain vocabulary with its addon.** Contribute additively through the
  framework seam instead of editing a framework/base-addon file to name a product
  concept. `tests/extcontrib` is the living REBAC extension example.
- **Docs teach intent; code explains concrete contracts.** Public APIs explain
  their current behavior in names, types, and docstrings. A mismatch requires
  [reconciliation](docs/guidelines.md#reconcile-code-docs-and-tests), not automatic
  acceptance of either stale prose or defective code.

If no Angee pattern exists, follow the underlying framework. When a new
architectural convention still has multiple plausible shapes after researching
the owners, ask the human architect to choose it.

## Architecture Gate

Before source edits for a structural change, refactor, new view, addon,
integration, model, or shared primitive, make these checks explicit in a working
note, plan, or user update:

- **Owner map:** name the owner of every fact being encoded. Reuse it or extend
  its missing behavior. Create an owner only when none exists at the right level.
- **Reuse evidence:** search at least two nearby addons/packages for the same
  concern. Name the existing components and upstream APIs to compose. Before
  adding an implementation, identify the concrete gap in those owners and why
  their native extension points do not already satisfy it.
- **Dependency check:** consult the stack; update the concern row and owning
  manifest together when changing dependencies.
- **Thin caller check:** keep business rules, persistence policy, and shared
  view mechanics out of dispatching entrypoints and pages.
- **Deletion check:** a DRY change must delete the targeted duplication; every
  refactor must simplify ownership, callers, or maintained structure. Name the
  copies, wrappers, obsolete paths, and workarounds it removes. Explain any growth
  by the missing behavior or owner it introduces and the simplification it buys;
  otherwise stop and find the smaller native shape.
- **Debt check:** identify rot encountered in the affected owners and callers,
  remove it at its source, and report any concrete migration or authorization
  blocker. Do not build a new layer on top of a known defect.
- **Naming check:** use one name per concept across code, routes, GraphQL,
  settings, menus, tests, and docs.

Keep extension mechanical: named hooks, explicit owners, deterministic ordering,
and fail-fast collisions. A failed gate requires changing the design; documenting
the violation does not make it acceptable.

## Read By Task

Read the applicable parent `AGENTS.md` files first. Stack instructions own
lifecycle and the stack root; workspace instructions own source slots; this file
owns framework architecture and repository work. A source checkout is not a stack
root. Template `AGENTS.md` files describe their rendered targets, not additional
rules for editing the template source.

| Document | Owns | Read when |
|---|---|---|
| [Development guidelines](docs/guidelines.md) | Research, implementation, DRY, naming, and reconciliation of conflicting evidence | All development work |
| [Opinionated stack](docs/stack.md) | Which dependency owns each concern | Before adding or replacing behavior or dependencies |
| [Glossary](docs/glossary.md) | Shared terms | Establishing vocabulary |
| [Backend guidelines](docs/backend/guidelines.md) | Python, Django, and backend extension rules | Backend or composer work |
| [Frontend guidelines](docs/frontend/guidelines.md) | TypeScript, React, and rendered behavior | Packages or addon web work |
| [Checks](docs/checks.md) | Command context, prerequisites, verification, and actual CI coverage | Before running checks or handing off |
| [Composer](docs/composer.md) | Composition flow and links to implementing owners | Changing composition or generated artifacts |
| [Agent methodology](.agents/README.md) | Reusable workflows and their tool adapters | Using or changing an agent workflow |

These links route reading; they do not imply every guide or tool definition has
already been loaded. Read the relevant owner before acting.

## Repository Role

The first question for any change is *what level does it belong to?*

- **Framework core** — the composition language, model/data toolkit, serving
  seams, and jobs seam inherited by every project. The core is not an addon.
- **Framework addon** — a reusable product capability that ships with Angee;
  also called a base addon. API protocols are capabilities too.
- **Consumer addon** — product logic for a specific project.

Never solve at the consumer level what the framework should own, and never push
product specifics down into the framework. Every fact lives at its owning level.

## Repository Layout

```text
.                    # consolidated framework source slot
├── angee/           # django-angee core: base/, data/, compose/, jobs/, serving seams
├── addons/          # standard angee.* folder addons with co-located web fragments
├── packages/        # schema-independent @angee/* packages and workshop/test tooling
├── examples/        # consumer addons and reference browser suite
├── templates/       # Copier project, stack, workspace, service, and addon sources
├── tests/           # core, addon, and template contract tests
├── docs/            # principles, operational guidance, and owner links
├── .agents/         # public shared agent methodology
└── README.md        # human entry point; this file is the contributor entry point
```

The `angee.*` namespace spans the core and folder addons. The core wheel is
`django-angee`; each addon is a source folder with `addon.toml`, not a separately
distributed Python package. Core dependencies belong to `pyproject.toml`, addon
dependencies to their manifests, and resolved versions to lockfiles. In a
materialized workspace the stack root owns the JS install; the repository's own
pnpm manifest also supports standalone package CI. See [the stack](docs/stack.md).

Personal-messaging bridges remain in the optional external
`angee-messaging-bridges` repository. The Go operator, `hatch-angee`, and
`strawberry-django-hasura` remain independently published. Private work-state is
an optional **workspace sibling slot**, not a required directory in this tree;
see [Where Knowledge Lives](#where-knowledge-lives).

## Area Rules

- **Core:** do not absorb product capability or addon vocabulary. Core changes
  require the full Python suite because every composed area can be affected.
- **Addons:** keep the manifest, capability, resources, permissions, and web
  fragment together. Generated SDL and `@angee/gql` belong to the composed host.
- **Packages:** all published framework packages, including `@angee/app`, remain
  schema-independent and never import project-generated `@angee/gql/*`.
  `refine` and `metadata` are independent leaves; `ui` uses both; `app` composes
  all three. Shared configuration belongs to `packages/tsconfig.base.json` and
  `packages/vitest.shared.ts`. Update the executable package/exports policy in
  `packages/app/src/architecture-guardrails.test.ts` deliberately when changing it.
- **Examples:** exercise public consumer extension seams; never make framework
  code depend on an example or privileged monorepo internals.
- **Templates:** edit the template source, not rendered copies. Preserve Copier
  answers and path semantics across the complete chain and update its tests.

Use [Checks](docs/checks.md) for area-specific commands and prerequisites.
Meaningful UI changes require browser verification as well as relevant tests.

## Mechanical Overrides

- Before structural refactors, remove dead code first.
- Re-read files before editing and after editing.
- If search results are unexpectedly sparse, inspect scope and ignored paths,
  then rerun a focused search; absence from one search is not proof of absence.
- Sort build-time iteration. Never put wall-clock time, random IDs, or
  filesystem-order dependence in emitted artifacts.
- A clean/reset command may delete only the configured generated runtime after
  verifying its generated sentinel. Preserve `*/migrations/` unless deletion is
  explicitly documented for that command and appropriate to that history.
- Put screenshots, logs, and disposable scratch in gitignored locations such as
  `.playwright-mcp/`, `test-results/`, or `playwright-report/`.
- User instructions control task scope and authorization, including requests not
  to commit or publish. A workflow does not grant additional permission.

## Run From The Root

An existing current or ancestor `angee.yaml` owns stack lifecycle. Never
initialize a stack under a source checkout. Resolve it with
[the workspace skill](.agents/skills/angee-workspace/SKILL.md#resolve-the-controlling-stack-root)
and pass it explicitly as `angee --root "$angee_root" ws ...`.

`angee dev` is the supported way to start the complete local stack. Run it against
the resolved stack root; never start Django, Vite, ASGI servers, workers, or
watchers by hand. One-shot management commands use the stack host's `manage.py`.
[Checks](docs/checks.md#composition-and-schema) owns their command context and
ordering. For a new branch, use the workspace skill's Create Workspace workflow.
Never `git checkout` or `git switch` inside a pinned workspace slot.

## Where Knowledge Lives

Durable project knowledge is checked in, not held only in private agent memory.

- **Principles and pitfalls:** extend the owning `docs/` guide with a terse rule
  and an owner link. Exact API details belong beside the implementing code.
- **Shared methodology:** reviewer prompts, skills, commands, and workflows live
  in public `.agents/`; private task history does not.
- **Private work-state:** resolve the current workspace through the workspace
  skill. Its optional work-state slot is `<workspace>/.work`, materialized as a
  Git clone or local-source symlink. Use its `plans/specs/`, `plans/`, `notes/`,
  and `handovers/` directories; do not create `angee/.work` inside this source
  slot. Follow the workspace's synchronization rules and the user's instructions.
- **No work-state source:** keep task-specific plans and handoff notes in the
  conversation. Do not invent a private repository/path or put private history
  into public docs or `.agents/`. Global defaults such as `docs/superpowers/**`
  are overridden and forbidden here.
- **History is not reference:** specs, plans, and notes recover prior intent.
  Verify their claims against current code before acting. Promote enduring
  lessons into the owning public guideline rather than leaving them only in
  private history.

## Definition of Done

- Every change satisfies the constitution; structural work also satisfies the
  architecture gate. Passing tests alone is insufficient.
- Behavior composes shared primitives and upstream libraries at the owning
  level. New implementation fills a demonstrated gap; it does not run alongside
  an existing implementation of the same concern.
- Refactors name the ownership, duplication, naming, or complexity defect they
  removed, what was deleted or simplified, and why any added structure earns its
  place. DRY refactors leave no copies of the targeted rule behind.
- Encountered rot and debt are removed at their owners. Any blocked cleanup is
  named with the required fix and blocker; affected work remains incomplete.
- Artifacts are regenerated from source; names agree across touched surfaces.
- Relevant [checks](docs/checks.md) have run, or the handoff states exactly which
  could not run and why. Evidence distinguishes local checks from CI coverage.
- Changed contracts and their documentation agree; no stale guidance is left
  recommending the superseded shape.
