# Operator addon structural audit

Scope: `src/angee/operator` (Python addon + `web/` TypeScript console).
Judged only against this repo's docs (`AGENTS.md`, `docs/guidelines.md`,
`docs/backend/guidelines.md`, `docs/frontend/guidelines.md`, `docs/stack.md`).
Codegen stubs (`web/__generated__`, `web/schema/operator.graphql`) are output and
not flagged.

- id: operator-001
  loc: src/angee/operator/daemon.py:92
  category: function-local / deferred import
  severity: high
  rule: backend/guidelines.md "Imports go at the top of the module ... Within `src/angee` these are the only function-local imports allowed — phase-1 deferrals and `TYPE_CHECKING` blocks."
  finding: `from graphql import ...` is deferred inside `introspect_sdl` with no marker; `graphql` is a hard dep (via `strawberry-graphql>=0.270`), so it is neither optional-at-runtime nor a phase-1 deferral.
  fix: Hoist `from graphql import build_client_schema, get_introspection_query, print_schema` to module top.
  status: open

- id: operator-002
  loc: src/angee/operator/web/src/roles.ts:1
  category: dead / lifted code
  severity: medium
  rule: AGENTS.md "Prefer deletion to abstraction"; guidelines.md "Avoid Red Flags / The code is bigger instead of smarter"; frontend/guidelines.md "Client-side gates are UX only. The server is the authorization boundary."
  finding: `roles.ts` (OPERATOR_ADMIN_ROLES et al.) is imported nowhere; it is speculative, TODO(G1/G2)-gated scaffolding for nav role-gating that does not exist yet, while the server REBAC gate is already the boundary.
  fix: Delete `roles.ts`; reintroduce the constants beside the role-filter primitive when G1/G2 actually lands.
  status: open

- id: operator-003
  loc: src/angee/operator/web/src/data/fixtures.ts:1
  category: dead / lifted code
  severity: medium
  rule: AGENTS.md "Prefer deletion to abstraction"; guidelines.md "Avoid Red Flags / The code is bigger instead of smarter."
  finding: `fixtures.ts` (~260 lines of sample snapshot/service/source/etc data) is imported by no story, test, or runtime module in the repo; there are no `*.stories.*` files in `operator/web`, so the fixtures it exists to feed do not exist.
  fix: Delete `fixtures.ts` until the Storybook stories that consume it are added in the same change; per docs the example/story is the documentation, but only when it is wired.
  status: open

- id: operator-004
  loc: src/angee/operator/web/src/index.ts:42
  category: DRY / code is bigger instead of smarter
  severity: low
  rule: guidelines.md "Avoid Red Flags / The code is bigger instead of smarter" + "Don't Repeat Yourself"; AGENTS.md DRY "Same shape in three places: extract the smallest boring primitive."
  finding: The eight console sections are enumerated four times in parallel — `routes[]` (8 near-identical blocks differing only by name/path/breadcrumb), `menus.children[]`, the `enOperatorBundle` titles, and `index.test.ts` `SECTION_PATHS` — so adding a section means editing four hand-synced inventories.
  fix: Derive `routes` and `menus.children` from one ordered section table (id, path, label, icon) so the routes/menu pairing and breadcrumbs are computed once.
  status: open

- id: operator-005
  loc: src/angee/operator/web/src/views/pages.tsx:14
  category: forwarding wrapper / thin indirection
  severity: low
  rule: backend/guidelines.md "A wrapper must prove it adds a real new concept. If it only forwards ... delete it." (Django-Native Rule, applied as the cross-stack thin-wrapper principle); frontend/guidelines.md "Use shared page, view, form, table, widget, and shell primitives before adding new local state."
  finding: `pages.tsx` exists only to wrap each section in `OperatorSectionFrame`, and `OperatorSectionFrame` itself only forwards `children` into `OperatorTransportProvider` (operator-section-frame adds no state, nav, or layout). Two indirection layers stand between the route and the section for one provider wrap.
  fix: Have `index.ts` reference the section components directly and mount `OperatorTransportProvider` once at the console-shell level (or collapse `OperatorSectionFrame` into the route component), removing `pages.tsx`'s `framed()` factory and the empty frame.
  status: open

## Adjudicated, NOT findings (recall-biased scanner candidates cleared)

- operator/models.py:23 `managed = False` abstract anchor — INTENTIONAL and
  correctly placed. The module docstring documents it as a table-less REBAC type
  anchor for `operator/connection`; the composer (`compose/runtime.py:_models_source`)
  emits a concrete `abstract = False` class carrying the REBAC `Meta`, so the
  `rebac.E009` check resolves. Mirrors the iam `// rebac:const=admin` pattern in
  `iam/permissions.zed`. Level (iam base-addon-adjacent operator base addon) is right.

- management/commands/operator_schema.py multi-word module name — NOT a naming
  violation. `commands/` is a structural directory Django discovers by filename;
  the file IS the command name, and the `<addon>_<verb>` prefix is the
  established repo idiom (`iam/.../iam_oauth_clients.py`). Naming rule targets
  role modules (`models.py`, `managers.py`), not command modules.

- daemon.py:78 / daemon.py:103 isinstance checks — NOT a type-switch wanting
  polymorphism. They are boundary validation of an untyped decoded-JSON `dict`
  from an external HTTP response, not a switch over the addon's own value types;
  this is the correct shape for parsing a foreign payload.

- transport.tsx / *Section.tsx `extends Record<string, unknown>` — NOT missing
  types. These are urql's required `Variables extends AnyVariables` constraint
  shape on typed mutation-variable interfaces; the fields are explicit. The lone
  loose alias `DaemonActionData = Record<string, unknown>` (run-action.ts:2) is
  documented as an intentionally op-varying mutation payload.

- index.ts uses `const operator: BaseAddon = {...}; export default operator` rather
  than `defineAddon` — NOT a deviation. The notes example addon
  (examples/notes-angee/.../web/src/index.tsx) does exactly the same; `defineAddon`
  is the host-composition (`createApp`) seam, not the per-addon manifest form.

- OperatorDaemon as a frozen dataclass with `from_settings`/`mint_token`/
  `introspect_sdl` — CORRECT primitive. Group behavior lives on the class that
  owns the daemon-connection data (the repo idiom), not loose module functions;
  it is a transport bridge, not persisted state, so it is rightly NOT a Django model.
