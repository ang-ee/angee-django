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
| strawberry-django | GraphQL types, resolvers, dataloaders, schema printing | Merge addon schema parts into named schemas, `crud`/`changes` shortcuts, emit SDL, serve per name |
| django-choices-field | Enum-backed model fields | `StateField` semantic wrapper |
| strawberry-django-aggregates | Aggregation and group-by resolvers | Addon-level `AggregateBuilder` wiring (per addon, e.g. notes) |
| strawberry-django-hasura | Expose Django models in the Hasura GraphQL dialect (`_bool_exp`/`_aggregate`/`x_by_pk`/`_set`) | Composes it as the per-model data-resource emitter (`hasura_resource`) |
| channels + uvicorn | ASGI/WebSocket transport and serving | GraphQL subscription mounting; uvicorn serves the composed ASGI app and sends the lifespan that enters the MCP mount's `http_app` lifespan (`angee.asgi`) |
| django-zed-rebac | REBAC engine, actor scoping, relationship storage, local and SpiceDB-compatible backends | Per-addon schema merge, reserved roles, actor resolver |
| django-sqids | Opaque external IDs | `SqidMixin`, `SqidField` (NULL-safe decode on joins), GraphQL boundary scalar |
| django-simple-history | Shadow history tables and revert | `HistoryMixin` marker |
| django-reversion | Versioned field snapshots and revert | `RevisionMixin` convenience API, composer-emitted model registration |
| cryptography | Encryption primitives | `EncryptedField` (Fernet at rest, secret-by-type) |
| django-import-export + tablib | Resource import/export resources, tabular formats, row cleaning, and row results | Tiered manifests, xref ledger, and frozen-tier policy |
| pyyaml | YAML parsing substrate | Resource loader reads `.yaml`/`.yml` resource files; django-yamlconf consumes project settings YAML |
| django-yamlconf | Django settings YAML overlays | `angee.compose.settings` loads `settings.yaml` beside `manage.py`; `Composer` applies addon `autoconfig.py` fragments |
| django-environ | Typed boot environment access and URL parsers | `angee.compose.settings` reads Angee bootstrap env vars |
| pyjwt[crypto] | JWT and JOSE verification | OIDC discovery and exchange orchestration |
| mcp (jlowin FastMCP v2) | MCP server — tool registration, JSON-RPC, StreamableHTTP ASGI app, bearer auth (`TokenVerifier`), per-call middleware | Mounts one StreamableHTTP app at `/mcp` via the `asgi.py` `http_mounts` seam (its `http_app` lifespan entered by `angee.asgi` via `router.lifespan_context`), authenticates the bearer to a REBAC actor with a `fastmcp.server.auth.TokenVerifier` and brackets each tool call in that actor; addon tools — incl. `GraphQLTool` operations executed under the actor (`angee.mcp.graphql`) — run scoped, and rebac authorizes |
| anthropic | Anthropic Claude API SDK — Messages API client, model catalogue, retries, typed SDK models | `agents_integrate_anthropic` maps Angee inference providers/models to the SDK and contributes the backend into `ANGEE_INFERENCE_BACKEND_CLASSES` |
| openai | OpenAI Python SDK — Chat Completions client, model catalogue, retries, typed SDK models | `agents_integrate_openai` maps Angee inference providers/models to the SDK and contributes the backend into `ANGEE_INFERENCE_BACKEND_CLASSES` |
| python-magic | MIME detection from file bytes | Storage finalize detection (requires the system libmagic) |
| vobject | vCard/iCalendar parse + serialise | `parties_integrate_carddav` parses CardDAV vCards into parties/handles/addresses (and serialises for round-trip) |
| uv | Python dependency resolution and workspaces | Workspace layout |

## Frontend

| Pick | Owns | Angee adds |
|---|---|---|
| React 19 | View library | Component conventions |
| TypeScript >= 6 | Language and type system | Branded boundary types |
| @refinedev/core | Resource registry, standard data hooks, react-query cache/invalidation, auth/i18n/live provider contracts | `@angee/data` maps emitted `angee.resources` metadata to refine resources; `createApp` mounts named providers and the TanStack Router binding |
| @refinedev/hasura + graphql-request 5 + graphql 15 | Hasura GraphQL data provider (`_bool_exp`, `order_by`, `_aggregate`, `_by_pk`, `_set`) and authored `meta.gqlQuery` / `meta.gqlMutation` execution | `@angee/data` pins `idType: "String"` and `namingConvention: "hasura-default"`, uses refine-compatible GraphQL document ASTs, and applies Angee session/CSRF or service auth at the transport boundary |
| graphql-ws 5 | GraphQL WebSocket lifecycle for the Hasura live provider and temporary authored transports | Endpoint derivation, connection params, and retry policy |
| urql React 5 + @urql/core 6 | Transitional authored-operation transport during the refine migration | Legacy provider stack and invalidation wiring until the remaining SDK data modules move to refine/codegen |
| GraphQL Code Generator (client-preset) + @graphql-typed-document-node/core | Generated TypeScript schema and operation types from emitted Django SDL and daemon-owned SDL, as `TypedDocumentNode` documents | Each project web package owns a `codegen` script that emits `runtime/gql/<schema>` from `runtime/schemas/<schema>.graphql`, routed to a Django schema by document filename (`documents.ts`/`documents.console.ts` → console, `documents.public.ts` → public); the operator web package owns a separate daemon client-preset run from `schema/operator.graphql` scanning only `documents.daemon.ts`; authored operations carry no hand-written result/variables types |
| TanStack Router | Type-safe routing and search params | `defineAddon` to `createApp` route composition and flat URL search codec |
| @refinedev/react-hook-form + react-hook-form + @hookform/resolvers + zod | Form state, submit lifecycle, and validation binding | `FormView` keeps Angee's declarative rendered DSL while delegating state/validation to refine/react-hook-form |
| TanStack Form | Transitional current `FormView` engine during the refine migration | Removed once `FormView` is rebound to `@refinedev/react-hook-form` |
| @refinedev/react-table + TanStack Table | Server-backed table state, sort/filter/pagination bridge, columns, grouping, selection | `ListView` and `BoardView` keep Angee's rendered controls and domain view modes while delegating standard table/data mechanics |
| TanStack Virtual | Row and column virtualization | Long-list wiring |
| nuqs | Type-safe URL query state | Remaining chrome query state such as top-menu tabs |
| i18next | Runtime i18n | Per-addon namespace convention |
| date-fns | Date and relative-time formatting | Date and timestamp widgets |
| use-debounce | Debounced React values and callbacks | Search and filter inputs |
| Tailwind 4 | Token styling engine | Semantic token set |
| tailwind-merge | Safe class merging | `cn()` helper |
| lucide-react | Icons | Name-referenced icon registry |
| Vite | Bundling, dev server, HMR | Project integration |
| @zed-industries/agent-client-protocol | ACP client — agent JSON-RPC session, prompt/cancel, session-update stream | WebSocket ndjson transport to a routed agent + assistant-ui runtime bridge |
| @assistant-ui/react | Chat thread UI — message store, composer, tool-call rendering | ACP-streaming runtime adapter and styled thread surface |
| streamdown | Streamed-markdown render for assistant chunks | Assistant message body in the agent chat |
| react-pdf (+ pdfjs-dist) | Inline PDF rendering (pdf.js) | storage file previewer |
| @vidstack/react | Inline video/audio player | storage file previewer |
| heic-to | Client-side HEIC/HEIF decode to a displayable image (current libheif-wasm) | storage HEIC previewer |
| pnpm | JavaScript dependency resolution and workspaces | Workspace layout |
| Node >= 22.13 | JavaScript build runtime | Project runtime |

## Hasura Dialect Rule

`strawberry-django-hasura`, the operator daemon SDL, `@refinedev/hasura`, and
`@angee/data` share one Hasura-default wire contract. Grouped resources must keep
the DDN/NDC-preview shape:
`<resource>_groups(group_by, where, having, order_by, limit, offset): [<resource>_group!]!`,
with each group returning a typed `key: <Model>GroupKey!` and the free
`aggregate: <Model>Aggregate!`. The stock `<resource>_aggregate` root remains
the unmodified refine/Hasura aggregate surface; grouped roots are authored
operations owned by the dialect adapters.

Future grouped features such as bucket ordering, bucket predicates, additional
date extraction, or JSON drill-down operators must be added at the dialect
owners together: the Django adapter, operator SDL, emitted resource metadata,
and the `@angee/data` authored operation helpers. Do not add frontend-only group
semantics or local provider dialects.

## Rendered Binding

`@angee/sdk` stays headless. `@angee/base` is the single rendered binding.

| Pick | Owns | Angee adds |
|---|---|---|
| @base-ui/react | Headless primitives: dialog, popover, menu, tabs, tooltip, field, toolbar, scroll area, and related UI | Styled binding and composition rules; controlled `open`/`onOpenChange` owns popover/dialog transition timing |
| @floating-ui/react-dom | Floating-element positioning and virtual anchors | Popover and menu anchoring |
| @angee/logo-react | Angee brand logo and cube marks | Brand lockup in the public shell |
| react-markdown + remark-gfm | Markdown rendering (GitHub-flavored) | Markdown widget preview |
| tailwind-variants | Variant recipes with slots | Component recipes |
| tw-animate-css | Tailwind 4 animation utilities | Motion tokens |
| cmdk | Command menu | Spotlight shell surface |
| react-day-picker | Calendar | Date widgets |
| react-resizable-panels | Split panes | Shell and inspector layouts |
| CodeMirror 6 (+ @codemirror/lang-json) | Text / Markdown / JSON editor | Markdown and JSON widget editors (shared `useCodeMirrorEditor`) |
| @xyflow/react | node/edge graph canvas | `@angee/base` `GraphView` shell |
| @dagrejs/dagre | directed-graph layout | `@angee/base` `GraphView` node placement |
| @dnd-kit | Drag and drop | Board and rail interactions |
| Native browser drag/drop | File drag enter/leave/drop events and `DataTransfer.files` | `@angee/base` upload drop target primitive |

## Tooling

| Pick | Owns | Angee adds |
|---|---|---|
| Cobra | `angee` CLI implementation | Dev supervisor, init, workspaces, templates |
| hatchling | Python wheel build | Package metadata conventions |
| ruff | Python lint and format | Repo checks |
| mypy | Python type checking | Strict backend checks |
| pytest + pytest-django | Backend tests | Synthetic project and integration fixtures |
| Faker | Test and seed data generation | Bulk lorem fixtures (e.g. `seed_lorem_notes`) |
| Vitest | TypeScript and React tests | Frontend unit checks |
| happy-dom | DOM environment for Vitest | Per-file env opt-in for hook and component tests |
| @testing-library/react | React component and hook test rendering | Provider-wrapped render and hook harnesses |
| Playwright | Browser tests | `@angee/e2e` harness: workspace-isolated runner, role `storageState` login, GraphQL `api` fixture, Page Object base (`docs/testing/e2e.md`) |
| Storybook | Component workshop | `@angee/base` and addon previews |
| GitHub Actions | CI | Build, lint, type, test gates |
| Copier | Project and addon templates | Angee templates |

## Proposed, Not Locked

| Pick | Role |
|---|---|
| Yjs + Hocuspocus | Collaborative editing |
| Celery + django-celery-beat | Queues and schedules |
| pgvector / sqlite-vec / python-igraph / lightrag-hku | Vector search and graph RAG |
| django-ninja + pydantic | Typed REST sidecars (callbacks, webhooks, health) |
| boto3 | S3-compatible storage backend (S3 / R2 / MinIO presigned IO) |
| @xyflow/react | Graph and canvas (node/edge) views |
| react-json-view-lite + ansi-to-react | JSON widget read tree, debug/log JSON + ANSI panels |
| simple-icons + @lobehub/icons | Brand and vendor SVG icon registry |

## Change Policy

- Add a dependency only with an owner row here.
- Remove a dependency by deleting its row.
- Swap a dependency by updating the row and explaining why in the change.
- Move proposed picks into a locked section before shipping code that depends on
  them.
