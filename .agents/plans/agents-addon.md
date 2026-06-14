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

### Frontend follow-ups (deferred)

- **Agent skill/MCP membership editor** — backend `setAgentSkills`/`setAgentMcpServers`/
  `setAgentMcpTools` exist; the console needs a multi-select relation widget to drive
  them (none in `@angee/base` yet).
- **Per-tab create defaults** — neither the Templates tab (`is_template=true`) nor a
  skill source (`kind="skill"`) is pre-filled on create, because `DataPage` has no
  create-default prop. Today both are visible fields (a switch / a single-option
  select). A small `DataPage` `createDefaults` prop wired to `FormView.initialValues`
  (which already exists) would close both.

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

## Dropped from the reference prototype

ACP chat runtime; FastMCP outbound server + decorator tool-registry; frozen `config`
manifest blob (replaced by explicit FK + typed `*_inputs`); `AccountInferenceModel`
per-account join (per-account availability deferred).

## Flags

- httpx (vendor inference backends / live model refresh) needs a `docs/stack.md`
  owner row — deferred, not added silently.
- `mcp`/`fastmcp` stays out of the lock; MCP tools are hand-registered this milestone.
- Operator provisioning pipeline is milestone 2.
