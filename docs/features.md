# Features

Angee pre-solves the cross-cutting problems every SaaS, ERP, or internal tool
re-implements — auth, permissions, audit, files, secrets, integrations,
real-time, agents, deployment — and ships them as framework primitives and
composable addons. You inherit the answers and spend your code budget on what is
actually unique to your product.

> **Angee is in early alpha preview.** Everything below is **prototyped and
> working end to end** — proven in platforms the team has shipped — and is being
> lifted into this open-source framework one addon at a time. Items marked
> **Planned** are designed and on the near roadmap.
> Because every component *and its permissions* are tested end to end, each new
> addon stands on a proven foundation: technical **investment**, not technical
> debt.

## What Angee is

- **A framework, not an app** — used to build SaaS, ERP (re-cut as the agentic
  "ARP"), and self-hosted personal apps. You compose it; you don't fork it.
- **Addon-based** — every capability is one addon: a Django app (abstract models
  + GraphQL + REBAC + resources) plus, when it has UI, a sibling React package
  `@angee/<addon>-base` (routes, views, widgets, menus, i18n).
- **Composed, not patched** — `angee build` merges every addon by Python MRO into
  a generated `runtime/`. Deterministic (byte-identical, CI-checked); no
  monkey-patching, no runtime registration.
- **Headless backend, React frontend** — Python owns data, permissions, and the
  API; React owns everything visual; their only seam is the typed contract the
  composer emits.
- **REBAC-enforced** — every operation passes a relationship-based permission
  check. There is no "skip permissions" path.
- **GraphQL-first** — CRUD is auto-generated from model declarations; custom ops
  are `@query` / `@mutation` / `@subscription` methods; the client gets fully
  typed TypeScript, never a hand-written API client.
- **Agent-native** — agents are first-class, permissioned principals, not
  bolted-on automation.
- **Self-deploying** — one Go operator runs the same `angee.yaml` as a dev *or*
  prod stack; humans and agents drive the same lifecycle.

## What you get — framework primitives

- **Compose** — the addon system and the `angee build` pipeline that turns
  abstract addons into one running app: concrete Django models, one GraphQL
  schema, one permission schema, typed TS.
- **Data modeling** — abstract models with opaque `sqid` IDs and a `StateField`,
  plus drop-in mixins for timestamps, audit, history, revisions, archiving, and
  tagging; `EncryptedField` for secrets at rest.
- **Permissions (REBAC)** — Zanzibar-shape authorization via `django-zed-rebac`:
  reads scope through the manager, writes check the instance; each addon ships a
  `permissions.zed` fragment the composer merges and `rebac sync` loads.
- **GraphQL API** — auto-CRUD, search, and aggregates from model `Meta`;
  real-time subscriptions (channels + uvicorn); persisted operations and
  typed-codegen output for the client.
- **UX framework** — one Refine-native app root with Angee composition contracts,
  resource metadata projection, and the single rendered binding: refine layouts,
  resource action surfaces (`ResourceList` / `ResourceCreate` / `ResourceEdit` /
  `ResourceShow`), rendered view modes (`ListView` / `FormView` / `BoardView` /
  …), field widgets bound by meaning, Tailwind-token theming, and per-addon
  i18n.
- **Resources** — tiered, idempotent reference and seed data
  (`master` / `install` / `demo`) declared in `AppConfig.resources` and applied by
  `manage.py resources load`.
- **Admin** — a Django admin auto-emitted for every model from the same `Meta`,
  REBAC-scoped like everything else.

## Batteries — the addon catalog

- **IAM** — the identity REBAC operates on: users, groups, machine `Service`
  accounts, hashed API keys, impersonation auditing, and an actor resolver that
  unifies session / token / machine-to-machine.
- **Storage** — files and blobs: content-hash dedup, pluggable backends (local +
  S3/R2/MinIO), MIME detected from the bytes, one presigned upload flow, and
  MIME-keyed previewers.
- **Knowledge** — a server-backed, permissioned, Obsidian-shape knowledge base:
  pages, typed properties, wikilink/embed edges, canvases. "Index, don't grep" —
  every change becomes queryable rows. **Graph RAG** (vector + graph retrieval)
  lands as a follow-up addon.
- **Integrate** — third-party systems: a vendor catalogue, accounts, stateless
  providers and stateful bridges, and signed inbound/outbound webhooks.
  **OAuth2 / OIDC** ships as the credential-source sibling and also powers SSO.
- **Agents** — agents as first-class REBAC subjects with an audited **ceiling** on
  what their runtime may do: pick a template, bind a model and an integration,
  mount skills, and chat. Process lifecycle runs on the operator.
- **Operator** — a thin Django bridge that hands the browser a connection to the
  Go daemon, plus a console to manage stacks, services, workspaces, sources, and
  secrets.
- **Connect** *(Planned)* — people and conversations: contacts and organisations +
  threads and messages across web, email, and agent channels (agents are
  participants).
- **Workflows** *(Planned)* — durable queue + scheduled jobs + state machines with
  a React-Flow canvas; every step is a permissioned operation, with
  human-in-the-loop and agent steps first-class.
- **MCP** *(Planned)* — an agent tool surface generated from the framework's
  persisted, REBAC-gated GraphQL operations.
- **ARP** *(Planned)* — ERP re-cut as composable addons (invoicing, accounting,
  procurement, CRM, HR) on shared primitives `money`, `sequence`, and `uom`.

## How you build & ship

- **Opinionated dev cycle** — build a slice frontend-first in Storybook against
  mocked data → author the backend slice → `angee build` → e2e. GraphQL
  operation names are the throughline across every stage.
- **Tested end to end** — pytest (including a YAML scenario runner that drives
  operations *as* a given user), Vitest, and Playwright; e2e asserts that
  permissions hold, not just that the UI renders.
- **Self-managed stacks** — the `angee` CLI and operator run the same `angee.yaml`
  as a dev or prod stack from one set of Sources; workspaces isolate feature and
  agent work; agents can drive the whole lifecycle.
- **Deterministic builds** — same inputs → byte-identical `runtime/`, gated in CI;
  generated output is never hand-edited.

---

For which libraries are wired versus proposed, see the
[opinionated stack](stack.md); for where each capability stands today, see
[Get Started → How much is built](howto/getstarted.md#how-much-of-this-is-built-today).
The shared vocabulary is in the [Glossary](glossary.md).
