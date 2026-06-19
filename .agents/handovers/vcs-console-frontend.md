# Handover: VCS bridge console frontend + GitHub repo typeahead

Status: historical handover, updated for the current MTI naming contract. The
canonical VCS child row is `integrate.VcsBridge`; legacy VCS child/query/client
names are superseded.

## Your task

Build the **`@angee/integrate` web console** for the VCS inventory and a
**"type a repo name" typeahead** to add repositories from a connected GitHub
account — the UX the architect asked for: *"when we choose a github account/
credentials we should be able to type a repo name like a foreign-key field with
search/typeahead."* The whole backend is done, committed, and tested (commits
`269801e`, `9b281e4`); you are wiring the existing GraphQL surface to UI.

## What exists (backend contract — already shipped)

GraphQL is in the **`console`** schema, admin-gated. Models behind it (runtime
names for `DataPage model="…"`): `integrate.VcsBridge`,
`integrate.Repository`, `integrate.Source`, `integrate.Template`.

**Queries** (camelCase fields): `vcsBridges`/`vcsBridge`,
`repositories`/`repository`, `sources`/`source`, `templates`/`template`
(all `OffsetPaginated`/node), and the typeahead:
```graphql
searchRepositories(vcsBridgeId: ID!, query: String!): [RepoCandidate!]!
# RepoCandidate { name org remote sshRemote defaultBranch visibility webUrl archived }
```
**CRUD mutations** (SDL-driven via `DataPage`, no authored docs needed):
`createVcsBridge`/`updateVcsBridge`/`deleteVcsBridge`,
`createSource`/`updateSource`/`deleteSource`, `deleteRepository`.
- `VcsBridgeInput { vendor: ID!, owner: ID!, backendClass: String, credential: ID, account: ID, config: JSON, webhookSecret: String }`
  — `backendClass` is the role-named backend selector (from
  `ANGEE_VCS_BACKEND_CLASSES`); `webhookSecret` is write-only.
- `SourceInput { repository: ID!, kind: String!, ref: String, path: String }`.

**Action mutations** (author in `documents.ts`, invoke from `<Action>`/buttons):
```graphql
addRepository(vcsBridgeId: ID!, name: String!): RepositoryType   # returns the created row
discoverRepositories(vcsBridgeId: ID!, org: String = ""): ActionResult   # { ok message }
syncVcsBridge(id: ID!): ActionResult
refreshSource(id: ID!): ActionResult
```

**The bridge flow:** create a `VcsBridge` with vendor/owner/credential and a
`backendClass` such as `github` or `local`; host-specific search scope lives in
the bridge-owned `config` (for example `github_org`).

## Frontend patterns to mirror (files to read first)

- `addons/angee/integrate/web/src/index.tsx` — the `BaseAddon`: routes (list + `/$id`
  detail), `menus`, `icons`. Add VCS routes + a menu child + icons here.
- `addons/angee/integrate/web/src/views/IntegrationsPage.tsx` — **the page shape to
  copy**: `<DataPage model routed>` with `<List><Column/></List>` and `<Form><Field/>
  <Group/><Action/></Form>`, actions wired via `useAuthoredMutation` +
  `ActionContext` (`ctx.record`, `ctx.refresh()`); status enum gotcha below.
- `addons/angee/integrate/web/src/documents.ts` — authored op pattern (typed query
  string + `Data`/`Variables` interfaces). Add the four VCS ops + `searchRepositories`.
- `packages/base/src/views/RelationPicker.tsx` + `packages/base/src/widgets/
  combobox.tsx` + `RelationField.tsx` — the **FK typeahead** to reuse/mirror for the
  repo search. RelationPicker queries a *local model*; the repo typeahead instead
  queries `searchRepositories` (a non-model list), so build a thin variant on the
  same combobox primitive: debounced input → `useAuthoredQuery(SEARCH_REPOSITORIES,
  {vcsBridgeId, query})` → option list (`name` / `org` / `webUrl`) → on pick
  call `addRepository` then `refresh()`. Reuse `use-debounce` (already in the stack).
- `addons/angee/iam/web/src/documents.ts` + a relation-picker consumer (storage drive
  switcher, see the `relation-picker-create` note) for a worked typeahead example.

## Deliverables

1. **`views/VcsBridgesPage.tsx`** — `DataPage model="integrate.VcsBridge"`:
   list (`displayName`, `backendClass`, `status`, `lastSyncCompletedAt`); form picking
   `owner`, `vendor`, `backendClass`, optional credential/account, and `config`
   (json), with `<Action>`s: `sync` (`syncVcsBridge`), `discover`
   (`discoverRepositories`).
2. **`views/RepositoriesPage.tsx`** — `DataPage model="integrate.Repository"`: list
   (`org`, `name`, `visibility`, `defaultBranch`, `webUrl`), delete. **Plus the add
   typeahead**: a control band button/field "Add repository" opening the
   search-as-you-type picker bound to `searchRepositories` for a chosen
   `vcsBridge`; picking a candidate fires `addRepository` and refreshes the list.
   (A `SourcesPage`/templates view is optional polish — repositories + the typeahead
   are the core ask.)
3. **`documents.ts`** — `SEARCH_REPOSITORIES` (query), `ADD_REPOSITORY`,
   `DISCOVER_REPOSITORIES`, `SYNC_VCS_BRIDGE`, `REFRESH_SOURCE` + typed
   `Data`/`Variables` interfaces (mirror the existing ones; CRUD stays SDL-driven).
4. **`index.tsx`** — routes (`integrate.vcs` list + `integrate.vcs.$id`,
   `integrate.repositories` + `$id`), a menu child under the Integrations app, icons
   (`lucide-react`: e.g. `GitBranch`, `Github`/`FolderGit2`).
5. **`index.test.ts`** + a vitest for the typeahead (happy-dom; mock the urql client
   — see `addons/angee/iam/web/src/index.test.ts` and the `verify-run-tests-not-just-
   typecheck` discipline).

## Gotchas (from prior console work)

- **Status enum asymmetry** (`console-action-dsl`): reads serialize as the UPPERCASE
  name (`ACTIVE`), but `Patch.status`/`set={{status:…}}` takes the lowercase value
  (`"disabled"`). So `set={{status:"disabled"}}` but `visibleWhen={(r)=>String(r.status)
  .toUpperCase()==="ACTIVE"}`.
- **`backendClass` is a backend selector** — the create form should render it as a
  select. Don't free-type it.
- **Regenerate the SDL before the UI can introspect the new types**
  (`runtime-sdl-missing-breaks-app`): from the repo root run
  `uv run examples/notes-angee/manage.py angee build && … manage.py schema`; the
  DataPage model-metadata reads `runtime/schemas/console.graphql`.
- `useAuthoredMutation<Data, Vars>` needs `Vars extends Record<string, unknown>`;
  import `Row` from `@angee/sdk` (not `@angee/base`).
- `webhookSecret` is write-only — never select it back.

## Verify

- `pnpm --filter @angee/integrate test` (vitest) + the workspace `tsc`
  (`pnpm --filter @angee/integrate exec tsc --noEmit` or the repo's typecheck script).
- Run the app and do it for real (`run`/`verify` skills): `angee dev` from the repo
  root → console → Integrations → create a GitHub credential/vendor-backed
  `VcsBridge` (`backendClass: "github"`, optional `config.github_org`, secret)
  → on Repositories, type a repo name, see live GitHub results, pick one,
  confirm the `Repository` row appears. Also exercise `discover` (bulk) and `sync`.
- Backend is green and committed; do not modify it unless the SDL needs a field the
  UI requires (then regenerate SDL + run `schema --check`).
