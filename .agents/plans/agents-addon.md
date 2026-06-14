# Agents addon — design & plan

A base addon (`addons/angee/agents/`, label `agents`) that owns the catalogue for
defining AI coding agents: the agents themselves (and their templates), the skills
they mount, the MCP servers/tools they reach, and the inference provider/model
catalogue they run on. It composes onto the existing `integrate` + `iam` seams and
adds no new runtime concerns of its own.

Provisioning (rendering an agent into an operator workspace/service) is a later
milestone; this one is **inventory + discovery + console**.

## Level

Base addon. Depends on `integrate` (which brings `iam`). Inference clients for real
vendors are an open registry, so a host can later add a per-vendor client addon the
way `integrate_github` adds a VCS client — agents ships only the `none` null client.

## Reuse (do not rebuild)

| Concern | Owner we plug into |
|---|---|
| Skill discovery from a repo | `integrate.Source` + `VCSIntegration.discover(source, marker, parse)` + `source_kind` output-model registration + `Manager.sync_from_source` |
| Skill *sources* (the repos + typeahead) | `integrate` VCS console (`Source`/`Repository`/`VCSIntegration`, `searchRepositories`, `addRepository`, `refreshSource`) |
| Inference provider over a vendor account | `integrate.Capability` (over `integrate.Integration`) + `iam.Credential` for the API key + `ImplClassField` for the backend impl (storage-backend pattern) |
| Model maker / branding | `integrate.Vendor` (already seeds anthropic/openai/google) as `InferenceModel.publisher` |
| MCP auth secrets | `iam.Credential` (`STATIC_TOKEN`/`OAUTH`) |
| Console / GraphQL | Strawberry `schemas` `console` bucket, `crud(...)`, `AngeeNode`, `ActionResult`, admin-gated by `iam.permissions.ADMIN_PERMISSION_CLASSES`; `@angee/base` DataPage + `@angee/sdk` `useAuthoredMutation` |
| Field/model primitives | `SqidField`, `StateField`, `ImplClassField`, `EncryptedField`, `RebacManager`, per-addon `permissions.zed` const-admin |

## Models (`agents`)

- **`InferenceProvider(integrate.Capability)`** `ipr_` — concrete capability over an
  `Integration`. `name`, `base_url`, `backend_class` (`ImplClassField` →
  `InferenceBackend`, registry `ANGEE_INFERENCE_BACKEND_CLASSES`, default `manual`).
  `backend` property resolves the impl; `refresh_models()` upserts its models from
  `backend.list_models()`. Credential drawn from `self.integration.credential`.
  rebac `agents/inference_provider` (field-backed on `integration`).
- **`InferenceModel`** `imd_` — `provider` FK (CASCADE), `publisher` FK →
  `integrate.Vendor` (null), `name` (wire handle), `display_name`, `description`,
  `model_use` (`InferenceModelUse`: chat/completion/embedding/multimodal/generation/
  image), `is_default`, `status` (`InferenceModelStatus`), `context_window`,
  `max_output_tokens`, `capabilities` JSON, `config` JSON. unique `(provider, name)`.
  `InferenceModelManager.sync_from_provider(provider)` upserts (no destructive prune;
  preserves agent FKs). rebac `agents/inference_model` (field-backed on `provider`).
- **`Skill`** `skl_` — `source_kind = "skill"`; `source` FK → `integrate.Source`,
  `name`, `description`, `path`, `metadata` JSON. `SkillManager.sync_from_source` →
  `discover(marker="SKILL.md", parse=parse_skill_meta)`. rebac `agents/skill`
  (field-backed on `source`).
- **`MCPServer`** `mcp_` — `name`, `description`, `placement`
  (`MCPPlacement`: internal/external), `url`, `transport` (`MCPTransport`:
  stdio/http/sse), `credential` FK → `iam.Credential` (null), `config` JSON.
  rebac `agents/mcp_server` (admin).
- **`MCPTool`** `mct_` — `server` FK (CASCADE), `name`, `description`,
  `input_schema` JSON, `enabled`. rebac `agents/mcp_tool` (field-backed on `server`).
- **`Agent`** `agt_` — `name`, `description`, `is_template` (indexed; Templates tab),
  `owner` FK, `instructions` Text (→ AGENTS.md/CLAUDE.md at provision time), `model`
  FK → `InferenceModel` (PROTECT, null), `skills` M2M → `Skill`, `mcp_servers` M2M →
  `MCPServer`, `mcp_tools` M2M → `MCPTool`, `service_template`/`workspace_template`
  FK → `integrate.Template` (PROTECT, null), `service_inputs`/`workspace_inputs` JSON,
  `service`/`workspace` Char (operator instance names, set on provision), `status`
  (`AgentStatus`), `last_error`. rebac `agents/agent` (owner + const-admin).

## Inference backend seam

Follows the Django/storage backend pattern: `agents/backends.py` holds
`InferenceBackend(provider)` base with `list_models() -> Sequence[InferenceModelSpec]`
and the built-in `ManualInferenceBackend` (no client; `list_models()` returns `()`).
`InferenceModelSpec` is a frozen dataclass (handle, display_name, model_use, …).
Registered `manual` → `ManualInferenceBackend` in
`autoconfig.SETTINGS["ANGEE_INFERENCE_BACKEND_CLASSES"]`, the same shape as
`ANGEE_STORAGE_BACKEND_CLASSES`. Vendor backends (anthropic/openai) wrap an HTTP
client and ship in their own addons (like a storage `s3` backend addon); they need an
httpx owner row in `docs/stack.md` and are deferred. Until then model rows are
hand-entered in the console (live `refreshProviderModels` is wired but no-ops on the
`manual` backend).

## Console

`schema.py` `console` bucket, admin-gated. CRUD + types for all six models, plus
actions: `refreshProviderModels(id)`. Skill *sources* reuse integrate's `sources`
query (filtered `kind="skill"`) and `refreshSource`/typeahead — no duplicate Source
CRUD here.

Frontend `@angee/agents` (`addons/angee/agents/web/`): menu groups
**Agents** (Agents, Templates) · **Skills** (Skills, Sources) · **MCP** (Servers, Tools)
· **Inference** (Providers, Models). DataPages mirror `@angee/integrate`.

## Build checklist (milestone 1)

- [x] `__init__.py`, `apps.py`, `autoconfig.py`
- [x] `backends.py` (`InferenceBackend` base + built-in `ManualInferenceBackend` + spec)
- [x] `skills.py` (`parse_skill_meta`)
- [x] `models.py` (6 models + managers + enums)
- [x] `permissions.zed`
- [x] `schema.py` (console types + crud + refresh action)
- [x] register `angee.agents` in `examples/notes-angee/settings.yaml`
- [x] `angee build` → `makemigrations base agents` → `migrate` → `rebac sync` → `schema --check` — all green
- [x] ruff + mypy on `addons/angee/agents` — clean
- [x] frontend `@angee/agents` console + host registration (Agents/Templates, Skills [read-only], MCP Servers/Tools, Inference Providers/Models). Agents/Templates split via a server-side `AgentFilter`/`AgentOrder` on the `agents` query. Verified: agents package `tsc` clean + host `vite build` clean (host-wide `tsc` is blocked only by operator's pre-existing daemon-codegen debt).
- [x] agents test module — `tests/test_agents.py` (6 tests): skill discovery via `sync_from_source`/`SKILL.md`, inference-model sync (stub + `manual` backend), `parse_skill_meta`; `ANGEE_INFERENCE_BACKEND_CLASSES` + `StubInferenceBackend` added to `tests/settings.py`/`conftest.py`. No regression (integrate/scheduler/compose + agents = 32 pass together).
- [x] **Milestone 1 shipped + live-verified.** Console renders end-to-end via Playwright (admin login, all 8 tabs, zero console errors); enum create form (Backend Class select) and the Skills→Sources tab confirmed.

GraphQL console CRUD/REBAC unit test is the remaining deferred test (needs all six concrete agents models + importing `agents.schema`); covered for now by the live render + `schema --check`.

### Frontend review outcomes (react-reviewer)

Composition contract verified empirically (one rail root, no multi-referenced route,
no icon collision). Fixed: SkillsPage list-only `DataPage` crash (added a read-only
`Form` — `DataPage` resolves form fields eagerly); `refreshModels` now surfaces an
`ok:false` business failure instead of a green toast; immutable relation pickers
(`integration`/`provider`/`server`) marked `createOnly`.

**Enum write-casing — FIXED** (the merge brought the framework's documented fix).
`backendClass`/`modelUse`/`placement`/`transport` now submit the lowercase write
value via a shared `useEnumOptions` hook (SDL metadata → `value.toLowerCase()`) +
`createOnly`. **Skills → Sources tab — DONE** (DataPage over `integrate.Source`
filtered to `kind=skill` + a `SourceFilter` added to the integrate console).

### Console-polish outcomes

- **Per-tab create defaults — DONE.** Added `DataPage.createDefaults` (wired to
  `FormView.defaultValues`, create-only) in `@angee/base`; the Templates tab seeds
  `is_template=true` and the skill-sources tab seeds `kind="skill"`.
- **Agent M2M membership (skills/MCP) — backend DONE.** `skills`/`mcpServers`/
  `mcpTools` are now `[ID!]` on `AgentPatch`; strawberry-django's update resolver
  `.set()`s them (verified by `tests/test_agents_graphql.py`), so the explicit
  `setAgent*` actions were dropped. The agent is fully configurable via `updateAgent`.
- **GraphQL console test — DONE.** `tests/test_agents_graphql.py` (3 tests): M2M
  attach/clear via `updateAgent`, agent-update admin gating, `refreshProviderModels`
  gating. (20 backend tests pass together with integrate VCS + scheduler.)

### Frontend follow-ups (deferred)

- **Agent skill/MCP membership editor (UI widget).** The backend is clean
  (`updateAgent` sets the M2M), but the `@angee/base` `many2many` widget reads a list
  of *ids* while `AgentType.skills` reads as nested `SkillType` objects — a read-shape
  impedance with no repo precedent. A clean editor needs either an ids projection on
  `AgentType` or a framework M2M-form convention; best designed alongside Milestone 2
  (when the selections actually render into a workspace).

## Review follow-ups (deferred, not yet actioned)

- **DRY the source-kind reconcile loop.** `SkillManager.sync_from_source` and
  `integrate.TemplateManager.sync_from_source` share the same
  `discover → update_or_create → prune → stamp` body (only marker/parse/defaults
  differ). At two copies it is below the repo's "three places" extraction threshold;
  when MCP-tool discovery (the third) lands, lift a `Source.reconcile(marker, parse,
  defaults_for)` into **integrate** (its owner) and have both managers declare only
  those three facts.
- **`InferenceModel.is_default` is a soft hint** — multiple defaults per provider are
  currently allowed; no consumer reads it yet. When one does, decide single-default
  semantics and let the DB own it (partial `UniqueConstraint` on
  `(provider, model_use)` where `is_default`, + clear-prior-default on write).

Note: the full `pytest` suite has a pre-existing collection-order failure on `main`
(`test_integrate_graphql.py` imports `integrate.schema` before `test_integrate_vcs.py`
registers the concrete VCS models) — unrelated to this addon; integrate tests run
per-file. The agents test module follows the same per-file concrete-model pattern.

## Milestone 2 — operator provisioning (planned)

Render an `Agent` into a running operator **workspace** + **service** from its
`workspace_template`/`service_template` (+ typed `*_inputs`) and `instructions`,
tracking the result in `service`/`workspace`/`status`/`last_error`.

**Architecture — server-orchestrated** (pivoted 2026-06-14 from browser-orchestrated).
`provisionAgent(id)` is one Django flow; the browser only triggers it and watches
live status. This unlocks provisioning for non-admins (the server acts on their
behalf) and keeps the credential value entirely server-side.

1. `provisionAgent` (admin-gated console mutation) resolves the agent's template
   inputs + credential (`provision_*_inputs`), then drives the daemon over its **REST
   API** via `OperatorDaemon` (admin bearer): `set_secret` → resolve template refs
   from `GET /templates` → `POST /workspaces` → `POST /services/create` (start=true) →
   `mark_provisioned`. Failure → `mark_provision_failed` (status=error). The daemon
   has a REST API, so Django uses REST — **not** a GraphQL client.
2. `deprovisionAgent` → `POST /workspaces/{name}/destroy?purge=true` + `mark_deprovisioned`.
3. **Live status is browser-side** via the daemon: the reused operator
   `WorkspacesSection`/`ServicesSection` (filtered to the agent's instance, wrapped in
   `OperatorTransportProvider`). The daemon exposes `onWorkspaceStatusChange`/
   `onServiceLogs`/`onWorkspaceLogs` subscriptions — switching the status widgets from
   poll to subscription is the next polish step.

`OperatorDaemon` gained REST methods (`set_secret`, `resolve_template_ref`,
`create_workspace`, `create_service`, `destroy_workspace`) on a shared `_request`.
`@angee/agents` depends on `@angee/operator` (web, for the status widgets) and the
agents AppConfig `depends_on` operator (backend, for the daemon bridge). The browser
daemon-create hooks (`useWorkspaceCreate` etc.) stay in `@angee/operator/runtime` for
the operator's own Templates pane, unused by agents now. Execution is **sync
render+register** (create registers fast; the container build/start is the daemon's
async job, surfaced via the status subscription) — no Django task runner needed.

**Reviewer fixes applied (pivot review, 2026-06-14):** partial-failure compensation
(`_render_agent` tears the workspace back down if the service render fails); an
idempotency guard (refuse provisioning an agent that already holds a workspace);
secret/auth predicate agreement (advertise `auth_mode`/`secret_name` only when a
usable secret exists); find-the-owner (`InferenceProvider.credential`/
`InferenceModel.credential` accessors — `Agent` no longer walks the
model→provider→integration→credential chain); typed `_RenderPlan` dataclass (was a
stringly-typed dict); dropped unearned daemon params (`name`/`purge`/explicit
`start`); URL-encoded path segments; reconciled the `OperatorConfig`/`OperatorDaemon`/
`mark_provisioned` docstrings to the server-orchestrated reality; failure-path test.
**Deferred:** `select_for_update` for true concurrency (the workspace guard covers the
common re-provision case); drive or drop the unused `AgentStatus.PROVISIONING` (the
sync flow has no observable intermediate); `OperatorDaemon.is_configured` guard dedup;
the `record as AgentProvisionRecord` projection cast (accepted, fields are optional).

### Build decomposition
- **Agent-runtime Copier templates** under `templates/` (the substance; don't exist
  yet): a workspace + service template taking `instructions` (+ skills/MCP/model) as
  inputs and rendering `AGENTS.md`/`CLAUDE.md` + runtime wiring.
- **Resolve the daemon template ref from the daemon, not Django.** The daemon owns
  the `template: String!` ref format — it emits it in its own `templates` listing as
  `TemplateDescriptor.ref`. The provision flow matches the agent's
  `workspace_template`/`service_template` (`path` + `kind`) against
  `useOperatorSnapshot({templates:true})` to get the ref, so the undocumented format
  stays owned by the daemon instead of being hardcoded in a Django `operator_ref()`.
- **Write-back action** (`provisionAgent` / `deprovisionAgent`) — admin-gated Django
  mutation; the only server-side piece. Persists `service`/`workspace`/`status`/
  `last_error` after the browser's daemon calls succeed (or fail). Django owns this
  state; the live runtime health is read from the daemon, not mirrored here.
- **Reuse + enhance the operator's daemon widgets — do NOT hand-roll in agents**
  (architect directive, 2026-06-14). The operator addon already owns the daemon UI
  (`WorkspacesSection`/`ServicesSection`, the `OperatorTransportProvider` +
  `useOperatorSnapshot`/`useOperatorAction` data layer, `StateTag`,
  `runDaemonAction`), but today `@angee/operator` exports only its `BaseAddon`
  default. Work lands **in `@angee/operator`**, then agents consumes it:
  - Export the reusable surface (transport provider + snapshot/action hooks + types +
    a *parameterizable* workspace/service status widget that can render either the
    full list or a single instance by name). Extract the presentational table from the
    Sections so the Section (full list) and the agents embed (one agent's instance)
    share it.
  - Add the missing create flow **in the operator** (finish the `TODO(S6)` in
    `TemplatesSection`): `workspaceCreate`/`serviceCreate` documents + a reusable
    provision hook/widget. The operator's own Templates pane and the agents console
    both call it.
- **Console provision flow** in `@angee/agents` — a *thin* consumer: embed the
  operator's reusable workspace/service status widget (filtered to the agent's
  instance names), trigger provisioning via the operator's reusable provision hook,
  then call the Django write-back. No bespoke daemon plumbing in agents.
- **Skill/MCP/model membership editor** (the M1-deferred M2M widget) — needed for a
  *useful* agent, but not for the first end-to-end provisioning slice (instructions +
  templates alone provision); sequence it after the vertical slice works.

### Progress (2026-06-14)
- **Operator reusable surface — DONE.** `@angee/operator/runtime` subpath barrel
  exposes the transport provider + `useOperatorSnapshot`/`useOperatorAction` +
  `StateTag`/`OperatorSection` + daemon types; `WorkspacesSection`/`ServicesSection`
  take an optional `names` filter + `title` so the same widget renders the full list
  (operator console) or one agent's instance (agents console). Create capability
  (`WORKSPACE_CREATE_MUTATION`/`SERVICE_CREATE_MUTATION` + input types) exported for
  reuse via `useOperatorAction`. (operator typecheck + 13 tests green.)
- **Backend write-back — DONE.** `Agent.mark_provisioned()`/`mark_deprovisioned()`
  own the status/instance-name transition; `provisionAgent`/`deprovisionAgent`
  admin-gated actions dispatch to them. Test + SDL regenerated. (mypy/ruff/build/
  schema --check/11 agents tests green.)
- **Framework `recordExtras` slot — DONE.** `@angee/base` `DataPage` → `FormView`
  gained a `recordExtras({recordId, reload})` slot, rendered below the form (outside
  `<form>`, so a panel's buttons never submit) for a saved record only. Reusable by
  any addon. (base typecheck + 91 view tests green.)
- **Agents consumer (Slice 4) — DONE.** `@angee/agents` depends on `@angee/operator`
  (first cross-addon web dep). `AgentProvisioning.tsx` is a two-layer panel: the
  outer (console urql context) owns the agent record read + `provisionAgent`/
  `deprovisionAgent` write-backs; the inner (inside `OperatorTransportProvider`, whose
  urql context is the daemon) resolves the daemon template ref from
  `useOperatorSnapshot({templates})`, runs `workspaceCreate`→`serviceCreate`→destroy,
  and embeds the reused `WorkspacesSection`/`ServicesSection` filtered to the agent's
  instance. Wired into the Agents tab (not Templates) via `recordExtras`. (agents +
  operator + base typecheck, 91 base tests, host build all green.)

### Reviewer fixes + deferrals (2026-06-14)
Applied from the arch/django/react review of the M2 diff: the daemon-shape decoders
(`resolveTemplateRef`/`toAnswerList`) + typed create/destroy hooks moved into
`@angee/operator/runtime` (`data/provision.ts`) so the daemon's owner decodes its own
shape and agents is the thin consumer (closes directive #4; also gives the handlers a
stable `.run`); provision records the workspace *before* the service so a service
failure can't orphan an unrecoverable workspace; `mark_deprovisioned` clears
`last_error`; the deprovision admin-gate is now tested; the form-column width is one
constant in `FormView`. **Deferred:** extract a shared presentational instance table
so the embed runs one daemon poller instead of three (arch); bound/guard
`workspace`/`service` length so a malformed daemon name degrades cleanly on Postgres
(django); re-indent the `<>`-wrapped `FormView` return (cosmetic).

### Agent-runtime Copier templates — AUTHORED (2026-06-14)
Built against the operator's real contract (studied `angee-operator/`): the operator
owns ingress via a service `route: {port, auth: forward}` block (replaces `ports:`,
publishes nothing, central Caddy forward-auths each upgrade with operator-minted route
tokens) and secrets via `${secret.<name>}` + admin-bearer `secretSet`. So the
templates carry **no** in-container caddy/verifier/HMAC the prototype hand-rolled.
- `templates/workspaces/agent-default/` (`kind:workspace`) — renders `AGENTS.md`
  (from `instructions`), `CLAUDE.md`→`AGENTS.md` symlink, `.mcp.json` (from `mcp_json`).
  Inputs: `agent_name`, `instructions`, `mcp_json`.
- `templates/services/claude-code/` + `templates/services/opencode/` (`kind:service`)
  — one container service, `route: {port:3007, auth:forward}`, `workspace://` mount,
  API key via `${secret.<name>}`; single-process `stdio-to-ws` Dockerfile.
- Validated: copier.yml YAML + `parse_template_meta` discovery (kind/name/inputs) +
  mcp_json default JSON. Jinja render is for the live test (jinja2 ships with Copier).

### Agent fields → template inputs — RESOLVER DONE (2026-06-14)
`Agent` owns the mapping (build on the merged `service_environment()` plumbing):
- `provision_workspace_inputs()` → `agent_name` (name), `instructions`, `mcp_json`
  (assembled from the agent's HTTP/SSE MCP servers via `mcp_config()`); structured
  fields win over same-named `workspace_inputs` keys.
- `provision_service_inputs()` → `model` (the handle) + **credential-driven auth**:
  `auth_mode` = `oauth` when the model→provider→integration credential is
  `CredentialKind.OAUTH`, else `api_key` (prefer OAuth); `secret_name` =
  `inference_secret_name()` (agent-scoped, `agent-<sqid>-inference`). The secret
  *value* is never here — only the name. `service_inputs` supplies template extras
  (`permission_mode`, `provider`) and loses to the structured keys.
- The **service template owns the name↔kind map**: claude-code renders
  `CLAUDE_CODE_OAUTH_TOKEN` for `auth_mode=oauth`, `ANTHROPIC_API_KEY` for `api_key`
  — so the resolver→template chain handles both auth modes end-to-end.
- Tested (`make_integration` extended with a `kind=` param): workspace inputs from
  fields, auth_mode from credential kind (static→api_key, oauth→oauth). ruff/mypy/14
  agents tests/build/schema green.
- **Next:** expose the resolver on `AgentType` (e.g. `provisioningInputs`) so the
  console reads it instead of raw `*_inputs`; then the operator-bearer secret sync.

### Remaining to render end-to-end
- **OAuth refresh deferred** (`OAuthCredentialHandler.refresh` is a stub — "wired in
  S3"). The synced OAuth token is a point-in-time snapshot; re-sync to the operator
  on refresh once iam implements it.
- **Discover the templates as `integrate.Template`s** + make them available to the
  operator — register `templates/` as a template source so the agent's
  `service_template`/`workspace_template` FKs can point at them.
- **Operator-held secret sync** — Django pushes the agent's `iam.Credential` value to
  the operator store via `secretSet` (admin bearer, server-side) so `${secret.<name>}`
  resolves; the browser never carries the key.
- **Live smoke check** needs a dev-stack restart (new `@angee/operator` dep + the
  `provisionAgent` SDL change). `WorkspaceCreatePreflight` is worth wiring before create.

## Dropped from the reference prototype

ACP chat runtime; FastMCP outbound server + decorator tool-registry; frozen `config`
manifest blob (replaced by explicit FK + typed `*_inputs`); `AccountInferenceModel`
per-account join (per-account availability deferred).

## Flags

- httpx (vendor inference backends / live model refresh) needs a `docs/stack.md`
  owner row — deferred, not added silently.
- `mcp`/`fastmcp` stays out of the lock; MCP tools are hand-registered this milestone.
- Operator provisioning pipeline is milestone 2.
