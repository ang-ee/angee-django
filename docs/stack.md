# Opinionated Stack

This file is the source of truth for the libraries Angee binds and what each one
owns. The stack is opinionated so product and addon work starts from settled
choices instead of re-litigating infrastructure. If a concern is listed here,
use the library's native shape and keep Angee as thin glue.

Dependency changes must update this file in the same change.

## How The Stack Is Locked

- `docs/stack.md` owns concern boundaries: which library owns which job, and
  what thin glue Angee adds.
- `pyproject.toml` owns Python package metadata, workspace membership, tool
  configuration, and declared Python dependencies. `uv.lock` pins the resolved
  Python graph. Use `uv add` / `uv lock`; do not use `pip install` by hand.
- `package.json` owns JavaScript package scripts and declared dependencies.
  `pnpm-workspace.yaml` owns workspace membership. `pnpm-lock.yaml` pins the
  resolved JavaScript graph. Use `pnpm add` / `pnpm install`; do not use npm or
  yarn.
- A dependency change is complete only when the concern row here and the
  relevant manifest or lockfile agree.

## Backend

| Pick | Owns | Angee adds |
|---|---|---|
| Python >= 3.14 | Runtime and typing | Project conventions |
| Django 6.0+ | ORM, migrations, admin, auth contract, app registry | Abstract bases and build-time composition into runtime apps |
| strawberry-django | GraphQL types, resolvers, dataloaders, schema printing | Emit schema from composed models and wire REBAC |
| django-choices-field | Enum-backed model fields | `StateField` semantic wrapper |
| strawberry-django-aggregates | Aggregation and group-by resolvers | Wiring to model metadata |
| channels + daphne | ASGI and WebSocket transport | GraphQL subscription mounting |
| django-zed-rebac | REBAC engine, actor scoping, relationship storage, local and SpiceDB-compatible backends | Per-addon schema merge, reserved roles, actor resolver |
| django-sqids | Opaque external IDs | `SqidMixin` and GraphQL boundary scalar |
| django-simple-history | Shadow history tables and revert | `HistoryMixin` marker |
| cryptography | Encryption primitives | `EncryptedTextField(secret=True)` |
| PyYAML + csv | Resource file parsing | Tiered idempotent resource loader and ledger |
| django-ninja | Typed REST routing and Pydantic I/O | Rare sidecar mounting for callbacks, webhooks, health checks, and presigned upload flows |
| pydantic | REST payload schemas | Nothing; used inside sidecars |
| python-magic | MIME detection | Storage wiring |
| pyjwt[crypto] | JWT and JOSE verification | OIDC discovery and exchange orchestration |
| uv | Python dependency resolution and workspaces | Workspace layout |

## Frontend

| Pick | Owns | Angee adds |
|---|---|---|
| React 19 | View library | Component conventions |
| TypeScript >= 5 | Language and type system | Branded boundary types |
| urql 5 | GraphQL client, normalized cache, subscriptions | Provider stack and invalidation wiring |
| graphql-ws 6 | GraphQL WebSocket lifecycle | Connection params and retry policy |
| TanStack Router | Type-safe routing | `defineAddon` to `createApp` route composition |
| TanStack Form | Form state | `FormView` binding |
| TanStack Table | Columns, sort, filter, grouping, selection | `ListView` and `BoardView` bindings |
| TanStack Virtual | Row and column virtualization | Long-list wiring |
| valibot | Schema validation | Server-emitted schema binding |
| i18next | Runtime i18n | Per-addon namespace convention |
| Tailwind 4 | Token styling engine | Semantic token set |
| tailwind-merge | Safe class merging | `cn()` helper |
| lucide-react | Icons | Name-referenced icon registry |
| Vite | Bundling, dev server, HMR | Project integration |
| pnpm | JavaScript dependency resolution and workspaces | Workspace layout |
| Node >= 22.13 | JavaScript build runtime | Project runtime |

## Rendered Binding

`@angee/sdk` stays headless. `@angee/base` is the single rendered binding.

| Pick | Owns | Angee adds |
|---|---|---|
| @base-ui-components/react | Headless primitives: dialog, popover, menu, tabs, tooltip, field, toolbar, scroll area, and related UI | Styled binding and composition rules |
| tailwind-variants | Variant recipes with slots | Component recipes |
| tw-animate-css | Tailwind 4 animation utilities | Motion tokens |
| simple-icons + @lobehub/icons | Brand and vendor SVGs | Icon registry entries |
| cmdk | Command menu | Spotlight shell surface |
| react-day-picker | Calendar | Date widgets |
| react-resizable-panels | Split panes | Shell and inspector layouts |
| CodeMirror 6 | Text and Markdown editor | Markdown widget |
| @xyflow/react | Node and edge canvases | Graph and canvas views |
| @dnd-kit | Drag and drop | Board and rail interactions |
| react-dropzone | File drop boundary | Storage upload widgets |
| react-json-view-lite + ansi-to-react | JSON and ANSI rendering | Debug and log panels |

## Tooling

| Pick | Owns | Angee adds |
|---|---|---|
| Cobra | `angee` CLI implementation | Dev supervisor, init, workspaces, templates |
| hatchling | Python wheel build | Package metadata conventions |
| ruff | Python lint and format | Repo checks |
| mypy | Python type checking | Strict backend checks |
| pytest + pytest-django | Backend tests | Synthetic project and integration fixtures |
| Vitest | TypeScript and React tests | Frontend unit checks |
| Playwright | Browser tests | End-to-end checks |
| Storybook | Component workshop | `@angee/base` and addon previews |
| GitHub Actions | CI | Build, lint, type, test gates |
| Copier | Project and addon templates | Angee templates |

## Proposed, Not Locked

| Pick | Role |
|---|---|
| Yjs + Hocuspocus | Collaborative editing |
| Celery + django-celery-beat | Queues and schedules |
| fastmcp | MCP tool surface |
| pgvector / sqlite-vec / python-igraph / lightrag-hku | Vector search and graph RAG |

## Change Policy

- Add a dependency only with an owner row here.
- Remove a dependency by deleting its row.
- Swap a dependency by updating the row and explaining why in the change.
- Move proposed picks into a locked section before shipping code that depends on
  them.
