# Opinionated Stack

This file is the source of truth for the libraries Angee binds and what each one
owns. The stack is opinionated so product and addon work starts from settled
choices instead of re-litigating infrastructure. If a concern is listed here,
use the library's native shape and keep Angee as thin glue.

Dependency changes must update this file in the same change.

## How The Stack Is Locked

- `docs/stack.md` owns concern boundaries: which library owns which job, and
  what thin glue Angee adds.
- Each distribution owns static `[project].dependencies` in its `pyproject.toml`.
  The framework root is the `angee` meta-distribution, pinning every member at one
  release. Addons declare compatible
  ranges and direct fork references. Native uv workspace/path sources select
  development members; `uv.lock` pins the resolved graph. `uv sync` installs
  packages; the operator runs explicit `compose` and `provision` jobs afterward.
  Use `uv add` / `uv lock`; do not use `pip install` by hand.
- Package `package.json` files own scripts and declared JavaScript dependencies.
  The installing workspace's `pnpm-workspace.yaml` owns membership and its
  `pnpm-lock.yaml` pins the resolved graph. In a materialized source workspace,
  the stack root owns the install; never run `pnpm install` in a source slot.
  Standalone repository CI uses the repository workspace. Use pnpm at the owning
  root, not npm or yarn; see [Checks](checks.md) for execution contexts.
  The shared [install policy](../packages/scripts/pnpm-policy.cjs) disables pnpm's
  optimistic repeat-install shortcut so native package `prepare` scripts run
  even when dependencies are unchanged. In particular, `@angee/metadata` owns
  the compiled headless module consumed by plain Node code generation.
- A dependency change is complete only when the concern row here and the owning
  Python/addon/package manifest plus the owning lockfile agree.

## Backend

The **local backend is the only supported REBAC permission backend**.
[Base autoconfig](../src/angee/base/autoconfig.py) selects it: field-backed and
constant-backed relations and SQL read scopes require the local Django rows.
See the [REBAC rules](backend/guidelines.md#rebac); do not mirror these facts
into a remote permission store.

| Pick | Owns | Angee adds |
|---|---|---|
| Python >= 3.14 | Runtime and typing | Project conventions |
| tomlkit >= 0.13 | TOML authoring that preserves fixture document structure | The [base testing extra](../src/angee/base/pyproject.toml) declares it for native addon distribution fixtures in `angee.base.testing.packages`; production package reads use native `tomllib` |
| packaging | PEP 508 requirement parsing/markers and distribution-name normalization | The native compiler projects active requirements onto declared component entry points; `extra=""` selects activation edges |
| tldextract >= 5.3, < 6 | Public Suffix List registrable-domain parsing, including private suffixes | Bundled PSL snapshot with request-time networking disabled |
| Django >= 6.1.2, < 6.2 | ORM, migrations, admin, auth contract, app registry, response security headers (`SecurityMiddleware`, `ContentSecurityPolicyMiddleware` with per-view `csp_override`) | Abstract bases and build-time composition into runtime apps; the optional server addon installs both security middlewares. [`base.expressions`](../src/angee/base/expressions.py) supplies an ordered JSON subquery across PostgreSQL/SQLite and adapts Django's text SHA256 to PostgreSQL's built-in digest without requiring pgcrypto. |
| strawberry-graphql (ang-ee fork) + strawberry-graphql-django (imported as `strawberry_django`) >= 0.89.2 | Strawberry: schema, object/input/enum/union/lazy types, scalars, extensions, the Channels subscription transport and the Django view; strawberry-django: GraphQL types, dataloaders, schema printing, nested query optimization through the active schema's merged definitions, and the create resolver, which without a pre-save hook persists through `manager.create(**kwargs)`; on a REBAC manager that composes `RebacQuerySet.insert`, so queryset factories and the candidate gate run for GraphQL creates. Upstream PR strawberry-graphql/strawberry-django#959 proposes persisting the same prepared instance the resolver validated | Merge addon schema parts into named schemas, public-ID decoding, `changes` subscription shortcuts, emit SDL, serve per name; the write backend passes no pre-save hook |
| graphql-core >= 3.3.1, < 3.4 | GraphQL input coercion, validation diagnostics and execution | The [GraphQL manifest](../src/angee/graphql/pyproject.toml) owns the version contract. `GraphQLSchemas.resource_filter` uses native `coerce_input_value` and `validate_input_value` before Strawberry argument conversion, preserving field-specific Django validation errors for stored conditions |
| django-choices-field | Enum-backed model fields, precise nullable descriptors, and runtime generic field subscriptions | Base owns `StateField` and the implementation field; its [manifest](../src/angee/base/pyproject.toml) pins the native correction |
| django-countries >= 9, < 10 | Configurable ISO 3166-1 country choices, overrides, localized names, and translated display names | `angee.parties.fields.CountryCodeField` preserves configured codes and names, stores alpha-2 as a plain string, and exposes the upstream choices through resource metadata |
| pycountry >= 26, < 27 | Complete ISO country code and official/common-name lookup | `angee.parties.fields.CountryCodeField` uses the upstream catalogue only when the configured django-countries catalogue has no match |
| strawberry-django-aggregates >= 0.14.0 | Aggregation, JSON-path grouping, canonical group naming/type helpers, blank-allowed choice buckets distinct from null, caller-owned to-one group expressions and to-one relation-key overrides (unreadable parents merge into one exact null bucket), group key encoders and exact grouped cardinality, plus in-memory grouping of computed rows over an abstract row model (`make_row_model`/`compute_row_aggregation`) | Declarative dimensions/measures, schema-owned bucket value mappings, public-ID key encoding and actor-scoped related-axis expressions |
| strawberry-django-hasura >= 0.14.0 | Hasura's full `order_by` enum with explicit, engine-independent null placement (`asc` nulls last, `desc` nulls first, `*_nulls_first`/`*_nulls_last`); caller-declared insert and update root arguments and input-extension fields forwarded in the write envelope; expose Django models in the Hasura GraphQL dialect (`_bool_exp`/`_aggregate`/`x_by_pk`/`_set`, exact `Decimal` filters, nested to-one filter paths, nested to-many `NestedInsert`), resource-local lookup types, lazy filter queryset preparation from the translated Django predicate, declared queryset sort aliases with lazy expression preparation from resolved ordering and stable paging ties, named generated resource members, request-bound grouped-expression providers, exact grouped-count roots, plus computed (non-model) sources via a `run_query` `RowSource`, including grouped roots over the declared group columns exposed as `HasuraResource.row_model` | Composes it as the model emitter (`hasura_model_resource`, incl. actor-safe related grouping and `lines=` editable-child nested inserts) and the pydantic computed-source emitter (`hasura_pydantic_resource`, incl. `groupable` columns whose query axes read that row model); finalizes one `DataResourceQuery` from the composed schema and executable capabilities, consumed by `@angee/metadata`'s `ResourceQuery` |
| pydantic >= 2.13 | Typed declaration defaults, validation/parsing and JSON serialization | Declared by the [base](../src/angee/base/pyproject.toml) and [GraphQL](../src/angee/graphql/pyproject.toml) contract owners. `base.implementations.ImplBase` derives config defaults; `base.schemas.forms` projects FormSpec; GraphQL owns resource envelope aliases, exclusions and computed Hasura rows |
| jsonschema[format-nongpl] >= 4.26 + referencing >= 0.37 | Native Draft 2020-12 validation, format assertions and reference-registry traversal | The [base manifest](../src/angee/base/pyproject.toml) owns these libraries. `base.validators` validates native schemas, `base.schemas.references` resolves root-local references, and `workflows.schemas` owns workflow structural proofs |
| Standard library `xml.etree.ElementTree` on Expat >= 2.7.2 | Parsing untrusted XML with [Expat's amplification protections](https://docs.python.org/3/library/xml.html#xml-security) | Consumers refuse document type declarations at their parse point; `angee.parties_integrate_carddav` composes WebDAV/CardDAV parsing and its `AppConfig.ready()` registers the Expat floor check (`angee.E035`) |
| jsonpointer >= 3.1 | RFC 6901 parsing, resolution and updates | `extraction.pointers` restricts traversal to JSON objects and arrays for retained evidence |
| pypdfium2 >= 4.30 + Pillow >= 11 | PDF text/raster acquisition and image decoding | `extraction` prepares bounded document carriers once for recognition |
| pydantic-ai-slim >= 2.13 | Native model/message/usage protocols, structured and multimodal one-shot requests, agent loops and MCP toolsets | `agents.InferenceModel.infer` owns the direct-request envelope, structured-output decoding and normalized usage while preserving native response types; `InferenceModel.require_usable` owns actor read access, deployment approval and declared model capability, and `objects.usable` selects these deployments with catalogue defaults first; `InferenceBackend` owns the shared endpoint and typed transient-error classification; vendor addons own SDK and transport policy. Ollama accepts configured local or remote endpoints with environment proxies and redirects disabled, shows the output schema in instructions, maps provider `keep_alive` and `generation_limit` through its OpenAI-compatible SDK request, and keeps `num_ctx` in the deployed Modelfile. `agents_runtime_pydantic` adds only the MCP extra and bounded sessions |
| Celery + Redis + django-celery-beat + billiard | Task transport, worker execution, retries, queue routing, and periodic dispatch; beat's schedule lives in the database (`PeriodicTask` rows) instead of a per-host schedule file | owned by the optional [jobs addon](../src/angee/jobs/pyproject.toml); the host/stack supplies broker topology and runs beat embedded in its one shared worker (`worker --beat`, which must not be scaled: one scheduler per stack). Jobs declares Celery and pins django-celery-beat's official Django 6.1 support commit until its next PyPI release; it keeps the `billiard>=4.2.1,<4.3` compatibility bound. The addon-owned `python manage.py celery` command lets Django discover source roots before entering Celery's native CLI. Its default task base is Celery's native `DjangoTask`; provenance contributes attribution when enabled. Code owns the schedule: addons declare entries in `CELERY_BEAT_SCHEDULE`, and `angee.jobs.scheduler.DatabaseScheduler` writes them at startup and prunes rows no longer declared; rows hold run state and the enabled flag, and a failed database re-read keeps the last schedule. `angee.jobs.enqueue.enqueue_task` sends on transaction commit, or immediately outside a transaction. [`task_time_budget`](../src/angee/jobs/timeouts.py) reserves settlement time below both worker limits. Jobs defaults `CELERY_WORKER_PREFETCH_MULTIPLIER` to `1`. Task bodies delegate state changes and authoritative row locks to model/manager owners; advisory task locks are optional |

Decisions remain open until answered. [`DecisionProposal`](../src/angee/decisions/contracts.py)
validates alternatives, record actions and continuation outcomes with Pydantic.
The addon records chosen keys; askers apply their actions through record owners.
It has no task or scheduler dependency.
| python-dateutil | RFC-5545 recurrence-rule parsing and expansion (`rrulestr`) | `angee.scheduling` owns recurrence — `RecurrenceField` (a validated RRULE column) + `Recurrence.occurrences(window)`, bounded, timezone-aware expansion in the project `TIME_ZONE` |
| phonenumbers | Region-aware telephone parsing, validation, matching, and E.164 formatting | `parties.Handle.normalize_value` parses phone/WhatsApp values with `region=None`, so canonical E.164 input requires a leading `+country` code; invalid, impossible, or region-unknown values use the digit-only comparison fallback, and signature evidence mines only `PhoneNumberMatcher` matches through the same owner |
| channels + channels-redis + uvicorn | ASGI/WebSocket transport and serving; Redis-backed channel layer for production fanout | The GraphQL Channels owner validates WebSocket Origins against the request origin and Django CSRF trusted origins, with loopback allowed in DEBUG; GraphQL subscription mounting; uvicorn serves the composed ASGI app and sends the host lifespan that enters explicit addon contexts; MCP contributes FastMCP's public `app.lifespan(app)` in every worker; in-memory channel layer remains dev/test only; change-group membership is a lease each live subscriber renews, so members orphaned by a restart expire (`angee.graphql.constants`) |
| django-zed-rebac >= 0.30.0 | Native REBAC engine, Zed composition/introspection, actor-scoped reads and writes over live ORM-backed relations, gated-field projections, memberships and stored relationship cleanup; declared base-manager preservation and one schema snapshot per queryset fetch; save-authorized Django uniqueness/constraint validation; public `proposed_forward_relationships`, `generic_target`, `model_identities`, `resource_id_attr` and `user_subject_type` identity/projection owners; shared stored-relationship `referencing()` queries; native database-alias and manager checks | The [base addon](../src/angee/base/pyproject.toml) owns the dependency; GraphQL adds its compatible native extra and the dev group adds CEL validation fixtures. Addon schema composition, caveat refusal (`angee.E024`), [filtered visibility](backend/guidelines.md#rebac), resource grant policy and IAM product surfaces compose those public owners. The swappable [Changeset](../src/angee/provenance/models.py) projects admitted actor tuples from the loaded schema. Native Django 6.1 fetch modes pass through the model construction chain |
| django-axes | Login failure throttling at Django's `authenticate()`/auth-backend signal seam | IAM composes the app, standalone backend, and middleware so password GraphQL login stays a thin `authenticate(request=...)` caller |
| django-sqids + sqids | Opaque external IDs and the native reversible codec | The [base manifest](../src/angee/base/pyproject.toml) declares both libraries; `base.models.identity.SqidMixin` and `base.fields.identity.SqidField` own model identity and NULL-safe join decoding; GraphQL owns its boundary scalar |
| django-simple-history >= 3.13 | Shadow history tables, field copying, and revert; declared by the [provenance manifest](../src/angee/provenance/pyproject.toml) | [`HistoryMixin`](../src/angee/provenance/contrib/angee/history.py) and [`StatementRecords`](../src/angee/provenance/statements.py) compose source/donor tracking and typed assertions; `ModelHistory`/`ModelHistoryManager` subclass 3.13's generator and bulk-construction internals (`bulk_history_create`, `custom_historical_attrs`), which sets the floor; statement registration is separate from history; [`changeset()`](../src/angee/provenance/context.py) attributes history to the Django-swappable [`Changeset`](../src/angee/provenance/models.py); captured addons declare `angee.provenance` for that default storage |
| deepdiff >= 9.1.0 | Native `DeepDiff` comparisons of nested remote/local and base/side projections | `angee.integrate` owns three-way sync classification; canonical JSON hashing stays with [`canonical_json_sha256`](../src/angee/base/serialization.py) |
| django.contrib.postgres | Postgres full-text search (`SearchVectorField`, `GinIndex`, `SearchQuery`) | `messaging.Fragment.search` — stamped once at fragment creation (content-addressed rows are immutable, so no trigger/queue); dedup indexes each unique text exactly once. Postgres-only: the SQLite test backend leaves the vector NULL |
| cryptography | Encryption primitives | The [base manifest](../src/angee/base/pyproject.toml) owns `base.fields.encrypted.EncryptedField` (Fernet at rest, secret by type) |
| django-import-export >= 4.4 + tablib | Native Dataset parsing, model field coercion, instance loading, import lifecycle, row results and transactions | Source grouping/diagnostic indexes, tier/xref/adoption policy and canonical ledger/grant hooks; models select native resource subclasses for domain manager persistence. `WorkflowDefinitionResource` delegates each row to `WorkflowManager.install_definition`; the selected-database resource transaction surrounds the complete load. The bounded declaration-constraint evaluator remains because Django Q.check fails open on database errors |
| pyyaml | YAML parsing substrate | Resource loader reads `.yaml`/`.yml` resource files; django-yamlconf consumes project settings YAML |
| ruamel.yaml | Comment/format-preserving round-trip YAML editing | The `AddonInstaller`'s `settings.yaml` `INSTALLED_APPS` install/disable edit — the one writer that must preserve operator comments and layout (pyyaml round-trips lose them); not used at boot |
| django-compose == 0.1.0.dev2 | Native component index/app closure, isolated selection inspection, settings composition, `Composable` declarations, emitted artifacts/receipts, verified serving, explicit provision/readiness, migration authoring and launcher | Angee adds base deployment glue and capability integrations; it does not maintain a second compiler. The [base manifest](../src/angee/base/pyproject.toml) owns its exact public PyPI pin; direct importing packages declare compatible ranges. Native uv records the resolved distribution artifacts in the lock. Optional compiler co-development uses a separate disposable check environment |
| django-yamlconf | Native YAML overlays, merge syntax and source provenance | `angee.base.conf` loads bounded authored/deployment settings; `django_compose.conf` composes ordered app contributions and owns strict error handling without losing structural provenance |
| django-environ | Typed boot environment access and URL parsers | `angee.base.conf` reads Angee deployment env vars and honors the standard service URLs a deployment injects — `DATABASE_URL`→`DATABASES`, `CACHE_URL`→`CACHES` — override-safe (an explicit project setting wins), falling back to the SQLite/Django floor when unset. |
| psycopg pool | Bounded PostgreSQL connections per process through Django's native `OPTIONS.pool` | `angee.base.conf` maps `ANGEE_DB_POOL`, `ANGEE_DB_POOL_MIN_SIZE`, `ANGEE_DB_POOL_MAX_SIZE`, and `ANGEE_DB_POOL_TIMEOUT` into the PostgreSQL database config when enabled. The stack template enables it only for production Django workers; Celery and management jobs keep Django's short-lived connection default. |
| sentry-sdk >= 2.71 | Error and event transport; its Django, Celery and logging integrations; `SENTRY_DSN` / `SENTRY_ENVIRONMENT` / `SENTRY_RELEASE` read from the environment | [`angee.base.conf`](../src/angee/base/conf/__init__.py) starts it when `SENTRY_DSN` is set (stack input `sentry_dsn`), after addon settings compose or before a composition failure propagates. Native integrations retain PII, frame locals and request bodies in the process. GraphQL's [autoconfig](../src/angee/graphql/autoconfig.py) excludes native Strawberry HTTP error capture through `SENTRY_DISABLED_INTEGRATIONS`; [`GraphQLConfig`](../src/angee/graphql/apps.py) suppresses its duplicate logger. Unexpected resolver errors arrive once through the logging integration as the schema sanitizer's record (class, path, frames; no exception value) |
| django-anymail | Vendor-neutral Django email backend API across transactional ESPs, plus the deterministic test backend | `angee.messaging` renders outbound `Message` parts and envelopes into `AnymailMessage`; deployments select an ESP through Django's native `MAILERS["default"]` plus `ANYMAIL`/`ANYMAIL_*` settings, while an unconfigured stack logs and declines delivery without touching Django's implicit localhost SMTP backend |
| requests >= 2.32 / urllib3 >= 2.2 | The HTTP transport under Anymail's requests-based ESP backends, and its exception types | `angee.messaging` email delivery reads the transport error Anymail chains: a connect timeout or a new-connection/DNS failure was never sent and retries; other transport failures after sending began settle as uncertain. No Angee code sends HTTP through requests |
| pyjwt[crypto] | JWT/JOSE signature + claims verification and JWKS fetch | OIDC id_token verification (`OAuthClientOidcProtocol.verify_id_token`); kept because `authlib.jose` is deprecated. The OAuth2 token exchange itself is owned by authlib |
| authlib >= 1.8.0 | OAuth2/OIDC client protocol — authorization-code + refresh-token requests, client authentication, PKCE (RFC 7636), and token revocation (RFC 7009); its `OAuth2Client` is an `httpx2.Client` | Thin per-`OAuthClient` `OAuth2Client` adapter behind the stable `OAuthClientProtocol` seam, driven over the integrate `PinnedTransport`, plus the non-standard JSON-token-body shim; id_token verification stays on pyjwt. Earlier releases build the client on httpx, which cannot drive an httpx2 transport |
| httpx2 >= 2.10, < 3 | HTTP client/transport for all integrate outbound calls — the maintained httpx fork Authlib builds its OAuth client on | `integrate.http.PinnedTransport` — an SSRF-pinned httpx2 transport that resolves once and dials a validated IP (judgement owned by `integrate.net.is_unsafe_address`) with system-store TLS from the stdlib context. Composed by `HttpClient` (the integration backends, including bounded method-preserving same-origin redirects) and by the OAuth client (handed to Authlib's `OAuth2Client`); the honest `Angee-Integrate/1.0` UA and an injected transport test seam ride on it |
| httpcore2 >= 2.10, < 3 | httpx2's low-level connection pool + network backend; each httpx2 release pins its exact httpcore2 | `integrate.http._PinnedBackend` subclasses `httpcore2.SyncBackend` and overrides `connect_tcp` to dial the validated IP; `PinnedTransport` swaps it into the pool's `_network_backend`. Named and bounded directly because the SSRF pin owns httpcore2's `SyncBackend`/`ConnectionPool` API, not just via httpx2 |
| httpx >= 0.28.1, < 0.29 | The legacy HTTP types pydantic-ai's `ModelSettings.timeout` contract names and its httpx-based provider paths raise | `agents` composes native timeout values and transport exception types for inference validation and retry classification; integrate's outbound HTTP does not use it |
| FastMCP >= 3.4.7 + mcp | MCP server framework and protocol SDK — tool registration, StreamableHTTP, bearer auth, middleware and native host/origin protection | The `angee.mcp` addon owns mounting and actor-scoped tool execution; its [manifest](../src/angee/mcp/pyproject.toml) owns the supported FastMCP and SDK versions |
| agent-client-protocol | ACP protocol, schemas, JSON-RPC and version negotiation (core SDK only) | The agents [manifest](../src/angee/agents/pyproject.toml) pins the SDK; `agents` adds the Channels transport and maps ACP methods to the session verbs |
| anthropic | Anthropic Claude API SDK — Messages API client, model catalogue, retries, typed SDK models | `agents_integrate_anthropic` maps Angee inference providers/models to the SDK and contributes the backend into `ANGEE_INFERENCE_BACKEND_CLASSES` |
| openai | OpenAI Python SDK — Chat Completions client for OpenAI and compatible endpoints, model catalogue, retries, typed SDK models | `agents_integrate_openai` maps Angee inference providers/models to the SDK and contributes the reusable backend into `ANGEE_INFERENCE_BACKEND_CLASSES`; compatible addons such as Ollama specialize it without another dependency |
| python-magic | MIME detection from file bytes | Storage finalize detection, with a filename fallback, and the inline-image rule (`require_image`), without one; both require the system libmagic, whose database decides which image types (such as HEIC) are recognised |
| vobject | vCard/iCalendar parse + serialise | `parties_integrate_carddav` parses CardDAV vCards into parties/handles/addresses (and serialises for round-trip) |
| IMAPClient | IMAP4rev1 protocol client — TLS/STARTTLS, LOGIN/XOAUTH2 auth, modified-UTF-7 folder names, SPECIAL-USE flags, typed UID SEARCH/FETCH responses | `messaging_integrate_imap` drives incremental mailbox sync over it with one `SyncStream` cursor per mailbox; MIME parsing stays on the stdlib `email` package |
| slack-sdk | Official Slack Web API client — user-token authentication, cursor pagination, typed API failures, and native rate-limit retry handlers | `messaging_integrate_slack` adds a retry deadline and sleep cap, serially polls one channel per workspace install and maps conversations onto chat threads in bounded, resumable pages; recently active thread parents carry independent reply watermarks so replies newer than the conversation history watermark still land. The supported deployment is a bring-your-own internal Slack app: internal apps retain the high-volume `conversations.history`/`conversations.replies` limits needed for backfill and polling, while distributed non-Marketplace apps are subject to the 2025 one-request-per-minute/15-object throttle and are not viable for this bridge. A future Socket Mode live layer must remain an optional phase-2 seam after user-scoped event delivery is verified; polling is the correctness foundation. |
| mail-parser-reply | Email body segmentation — splits a plain-text body into replies at multi-language attribution headers ("On …, X wrote:", Outlook From:-blocks, "-----Original Message-----") and detects signatures (the RFC 3676 `--` delimiter and salutation tails like "Best regards,") and trailing disclaimers | `messaging_integrate_imap`'s `split_plain_text` drives it with a curated language set (`cs`, `de`, `en`, `es`, `fr`, `it`, `nl`; the parser module says why the rest misread English) to role-tag body/quoted/signature parts, tagging an older reply's signature as quoted; quote-marker stripping stays local so a quoted paragraph content-addresses to the original body's `Fragment` rows |
| neonize | WhatsApp Web multi-device protocol — whatsmeow (Go) via a bundled platform library: QR-code device pairing, the encrypted session store (SQLite), connect/message/receipt event callbacks, media download + decrypt | `messaging_integrate_whatsapp` declares process isolation: each live channel runs in a fresh interpreter through `angee.integrate.session_process`, so native failures cannot abort other accounts or the Celery worker. Cross-process task locks are required. Events map through its parser onto `Message.objects.ingest`; its backend key contributes the `whatsapp` segment to `angee.integrate.live.session_store_path` (`ANGEE_DATA_DIR/<backend key>/<sqid>`) |
| qrcode[pil] | QR code generation — segment/error-correction encoding, PNG rendering via Pillow | Worker-only `angee.integrate.session._qr_data_uri` renders pairing payloads to PNG data-URIs carried in bridge `sync_progress`; the `[pil]` extra declares the Pillow renderer directly rather than leaning on a vendor SDK's transitive Pillow |
| telethon | Telegram MTProto client protocol — user-account auth (QR + 2FA), session persistence, update/event callbacks, media download | `messaging_integrate_telegram` runs one live session per channel on the dedicated `telegram` queue; `angee.integrate.live.session_store_path` owns its store convention at `ANGEE_DATA_DIR/<backend key>/<sqid>`, and events map through the addon's identity rules onto `Message.objects.ingest`. Use the published PyPI release with `>=1.44`; the addon owns Telegram protocol adaptation through Telethon |
| discord.py | Discord Gateway bot client — bot-token login, Gateway events/reconnects, REST history, and rate-limit handling | `messaging_integrate_discord` runs one sanctioned bot connection per channel on the dedicated `discord` queue and maps every invited guild channel (plus DMs sent to the bot) into threads. Enable the privileged `MESSAGE_CONTENT` intent in the Developer Portal. A bot cannot read the user's personal DMs or uninvited guilds; self-bot/user-token access violates Discord's Terms of Service and is explicitly out of scope. |
| signal-cli >= 0.14.6 (system binary) | Signal linked-device protocol, encrypted account store, JSON-RPC receive stream, and attachment download | `messaging_integrate_signal` runs one native signal-cli child per channel on the dedicated `signal` queue and keeps its config under `ANGEE_DATA_DIR/signal/<sqid>`. Install the native Homebrew build on supported macOS hosts or the upstream `-Linux-native` release on Linux rather than adding a Python/JRE dependency. Pin the exact binary release in deployment, monitor upstream releases, and update inside Signal's roughly three-month client capability clock: an obsolete linked device can be remotely unlinked, and no version pin is durable indefinitely. `SIGNAL_CLI_BIN` may select the pinned executable path. |
| matrix-nio[e2e] + vodozemac | Matrix client protocol and E2EE — password login, sync, lazy-member filters, authenticated media, Olm/Megolm decryption of encrypted rooms and attachments over the audited Rust vodozemac ratchets (wheels for every supported platform, no C toolchain) | `messaging_integrate_matrix` puppets one user's own Matrix account on the dedicated `matrix` queue and keeps nio's peewee crypto store under `ANGEE_DATA_DIR/matrix/<sqid>`. ISC + Apache-2.0. vodozemac owns the native cryptography; nio ships no cross-signing, so the addon's `recovery.py` ports mautrix's MPL-2.0 SSSS + self-signing round onto pycryptodome (`pycryptodome` and `unpaddedbase64` are declared directly because that module imports them). E2EE is mandatory and imports fail fast when its boundary is incomplete. |
| cryptg | Native AES-IGE for MTProto — the encryption hot path | Declared directly rather than via Telethon's optional extra, so the maintained native implementation is locked instead of falling back to pure-Python `pyaes` on a long-lived ingester |
| markdown-it-py | CommonMark tokenizer with source line spans (block token `.map`) and native inline extension rules | `knowledge` slices doc sections by heading without re-rendering — the `MarkdownPage` structure methods (`parse_outline`/`outline`, `section_range`, `spliced_section`, `spliced_unique`) shared by the `outline` read field and the section-anchored patch write. `knowledge_integrate_notion` translates vendor tags through an inline rule while preserving Markdown code and escapes; Python's `HTMLParser` owns tag attributes. |
| uv | Python dependency resolution and workspaces | Workspace layout |

Money representation: money stays native — a `DecimalField` (default
`max_digits=18, decimal_places=6`) paired with `MoneyField`'s `currency_field`
path declaration, never a money library. `angee.money` owns the currency
catalogue, dated exchange rates, and conversion; the reference currency is a
required project setting. Contextual rate histories carry an explicit canonical
record context and reference currency, never fall back to global history, and
resolve source priority before effective date.

State-transition library choice: `angee.base.models.transitions` is deliberately owned
in-repo. `django-fsm-2` and `viewflow.fsm` were evaluated and rejected because
Angee's guard is REBAC permission-as-query with a settings-backed policy overlay
and field-owned assignment guards, which a user-callable permission hook cannot express.

Tree library choice: `HierarchyMixin`'s materialized path is deliberately owned
in-repo. `django-tree-queries` and `treebeard` were evaluated and rejected
because the indexed path-prefix subtree test composes into the Hasura
`_bool_exp` filter dialect and REBAC subtree scoping as a plain column predicate;
recursive CTE ownership does not.

Implementation-registry choice: [`angee.base.implementations`](../src/angee/base/implementations.py) owns
Angee's settings-keyed composition contract, shared by `ImplClassField` and
rowless selectors. Each base names its setting. The bound Django field validates
its full mapping; addons register native system checks for rowless contracts.
Checks and choices project that same mapping, without a registry-of-registries. Native enum construction belongs to
[`ImplClassField`](../src/angee/base/fields/implementation.py); model-owned picker
policy and GraphQL adaptation compose through `angee.graphql.impl`. Python entry points were
evaluated and rejected because composition facts belong to project settings,
not package metadata.
Declared function hooks belong to [`angee.base.hooks`](../src/angee/base/hooks.py): addons
register Django checks for the settings they own, and callers use `resolve_hook`
or `resolve_hooks`. Resource sources and trigger sources are implementation class registries,
not caller-maintained loaders.

**REBAC base managers.** Models declare their base manager through Django's
`Meta.base_manager_name`; the pinned REBAC revision preserves declared managers
across abstract-fragment and concrete-parent inheritance, and requires a `TrackedQuerySet`.
[`AppendOnlyModel`](../src/angee/base/models/append_only.py) declares its guarded base manager
and checks that its default manager composes `AppendOnlyQuerySet`.

Audit-history exclusions: `django-easy-audit` was evaluated and rejected because
GPL code is incompatible with a framework composed into commercial consumers.
`django-reversion-compare` was evaluated and rejected for the same GPL reason;
`django-simple-history` owns snapshots and revert.

### Stack serve mode

Both stack templates take `serve_mode: development | production`. The framework
template also chooses `runtime_mode: process | docker`; the instance template
remains Docker only. Production serving requires Docker mode; the framework
Copier validator marks production with process mode invalid. A process render
still uses the complete development stack if validation is bypassed. Framework development
runs the autoreloading ASGI `runserver` and Vite HMR. Its single process
serializes synchronous GraphQL requests, so local page-burst timings do not
represent production concurrency.
Instances keep their built SPA and single uvicorn worker in development.
Production uses `manage.py serve` to run the configured `angee.server.application`
under uvicorn without autoreload. GraphQL's ready-phase `SchemaIntegration` emits
SDL during compose; `schema --check` is read-only. Serving children consume verified output
without regenerating artifacts. Development composition runs in the native
launcher parent before its reloader child.
`serve_workers: 0` uses CPU count (at least two); an explicit value must be at
least two. Uvicorn starts the ASGI lifespan in every worker. The MCP session
manager and process memory are worker-local; Redis owns shared cache and channel
fanout, and Celery beat stays in its one separate worker service.

Production framework stacks build the frontend after codegen with
`pnpm --dir <web_path> build`. Caddy serves `dist` with an `index.html` SPA
fallback, proxies declared Django paths, and strips `/operator` only for the
narrow `/operator/graphql` route. It sets the operator allowed origin to the
stack's browser origin. The inner Caddy waits for the edge's Compose alias on
public ingress and trusts its connected Compose network CIDRs with strict
right-to-left client IP parsing. It forwards the verified client IP as a single
`X-Forwarded-For` address. Django trusts the connected default network CIDR for
uvicorn's forwarded headers. Container IP changes within either network do not
leave stale trusted addresses; each container derives its CIDR at startup.
Localhost has no edge hop for the frontend. The local instance template still binds its
ingress to localhost. The public `/operator/graphql` route is unauthenticated
at the edge and reaches the operator daemon, which holds the Docker socket;
expose this route only with the operator's own request authorization in place.

Production Django enables psycopg's pool with zero minimum connections,
`db_pool_max_size: 2` per worker and `db_pool_timeout: 5` seconds by default.
The pool default's concurrency rationale lives beside
[`angee.base.conf`](../src/angee/base/conf/__init__.py). The earlier 16-connection page-load count
came from persistent per-thread connections before pooling, not 16 simultaneous
connections needed by one worker. The pool waits at most five seconds for a
free connection before raising a pool timeout error. Confirm the bound under a
page burst with a live subscription. Size the stack so **`serve_workers ×
db_pool_max_size + Celery connections + management-job connections ≤ PostgreSQL
max_connections`**, with headroom for administration. The actual worker count is CPU count when the
input is zero. Celery and one-shot management jobs have no pool and close
connections after use; development also has no pool. Django persistent
connections remain disabled under ASGI. A `DATABASE_URL` that supplies
non-zero `CONN_MAX_AGE` fails when the pool is enabled; zero is accepted by
Django's native pool. An explicit `DATABASES` project
setting remains authoritative. The framework template's Playwright image must
match the `@angee/e2e` version in `pnpm-lock.yaml`, enforced by the stack
template test.

Web processes bound each PostgreSQL statement to `ANGEE_WEB_STATEMENT_TIMEOUT`
seconds (default 60, `0` disables), so a query outlives its request by at most
that long; [`angee.server`](../src/angee/server/signals.py) applies it to every connection the
serving process opens, in development and production. Celery workers,
migrations, imports, the operator's jobs and other management commands never
load that entrypoint and keep the server's own timeout. Change it in project
settings or through the `ANGEE_WEB_STATEMENT_TIMEOUT` environment variable.

## Frontend

| Pick | Owns | Angee adds |
|---|---|---|
| React 19 | View library | Component conventions |
| TypeScript >= 6 | Language and type system | Branded boundary types |
| valibot | Runtime schemas, parsing and inferred wire types for generated metadata and opaque GraphQL JSON values | Domain owners declare supported contracts and preserve the permitted extension keys |
| ajv >= 8.20 + ajv-formats >= 3.0 | Draft 2020-12 JSON Schema validation, discriminated unions and format assertions | `@angee/ui/views/json-schema` composes the FormSpec annotation vocabulary and maps validation issues into React Hook Form; the dynamic compiler and formats stay behind this optional subpath. Ordinary FormSpec parsing uses the generated standalone validator |
| json-schema-to-typescript >= 16 + Ajv standalone + esbuild >= 0.28 | Native FormSpec type and validator source generation | `packages/ui/scripts/generate-form-spec.mjs` consumes the canonical `base/schemas/form_spec.schema.json`, emits wire types and standalone browser validation, and bundles only native runtime helpers; generated ordinary-form code imports no compiler or formats |
| @refinedev/core | Resource registry, standard data hooks, react-query cache/invalidation, auth/i18n/live provider contracts | Angee projects emitted `angee.resources` metadata to refine resources and mounts one composed `<Refine>` root with named providers and the TanStack Router binding. The shared [pnpm install policy](../packages/scripts/pnpm-policy.cjs) supplies core's omitted `graphql` package metadata so its public `DocumentNode` types resolve against Angee's GraphQL 16 transport instead of a provider-private copy. It backports upstream [Refine PR #7426](https://github.com/refinedev/refine/pull/7426) to 5.0.12 so only a transport that reads the lazy signal opts into Query cancellation. |
| @refinedev/hasura + graphql-request 5 + graphql 16 | Hasura GraphQL data provider (`_bool_exp`, `order_by`, `_aggregate`, `_by_pk`, `_set`) and authored `meta.gqlQuery` / `meta.gqlMutation` execution | Angee pins `idType: "String"` and `namingConvention: "hasura-default"`, uses refine-compatible GraphQL document ASTs, and applies session/CSRF or service auth at the transport boundary |
| graphql-ws 6 + 5 | GraphQL WebSocket lifecycle for the Hasura live provider and daemon-owned operator transport | Endpoint derivation, connection params, retry policy, and the operator daemon subscription + raw log socket transport — request/response now rides a Refine `operator` data provider, leaving only the intrinsically streaming surfaces on this ws transport. Two majors resolve honestly: the Hasura live provider pulls `graphql-ws@6` (the peer of `@refinedev/hasura@7`), while the operator daemon transport pins `^5.16.2` |
| GraphQL Code Generator (client-preset) + @graphql-typed-document-node/core + graphql-tag | Generated TypeScript schema and operation types from emitted Django SDL and daemon-owned SDL, as `TypedDocumentNode` documents; `graphql-tag` parses the schema-independent core document | `@angee/app` owns the one `angee-web-codegen` CLI: it reads `runtime/web/manifest.json`, generates each Django schema from `runtime/schemas/<schema>.graphql` (routing documents by filename: `documents.ts`/`documents.console.ts` → console, `documents.public.ts` → public), derives authored action/aggregate/group/delete-preview/save documents, and emits the composed `runtime/web/app.ts`. The operator daemon joins the same pass as an external `web/package.json` `angee.codegen` entry — its committed SDL read straight from the operator package, scanning only `documents.daemon.ts`, with a bare `typescript` types module the console re-exports. Addon-authored operations carry no hand-written result/variables types; the framework's `BaseImplChoices` is hand-typed against its core-owned Python projection so the React packages remain independent of a composed schema fixture. |
| TanStack Router | Type-safe routing and search params | `defineAddon` to `createApp` route composition and flat URL search codec |
| react-hook-form >= 7.81 | Native values, dirty/touched state, validation execution, errors and submission | Angee field/relation/required policy, action dialogs, atomic line diffs and Refine core read/write transport; resetDefaultValues advances submitted defaults while preserving later edits |
| TanStack Table | Native pagination/sorting/selection state, row models and commands | Thin adapters to `@angee/metadata`'s ResourceQuery for predicates, row accessors and group identity; Refine transport, route/favorite state and grouped/board presentation; single-sort URLs |
| TanStack Query | One native authored-read cache, finite/infinite options, observers, retries, cancellation and mutation state | Provider/document/variables identity, canonical model invalidation interests, deduplicated Refine auth/notification policy and operator snapshot cache writes. Messaging owns fixed-window reads and retained-ID revalidation over native InfiniteData; see [migration notes](frontend/upstream-reuse.md) |
| TanStack Virtual >= 3.14.10 | Row and column virtualization, native end anchoring | Long-list wiring; transcripts compose the native `anchorTo: "end"` lifecycle |
| nuqs | Type-safe URL query state | Remaining chrome query state such as top-menu tabs. Routed fixtures use its native TanStack Router adapter; the shared [install policy](../packages/scripts/pnpm-policy.cjs) pins and patches 2.10.1 to read the complete query snapshot from router history, preserving unrelated search keys in memory histories. |
| i18next | Runtime translation, plural defaults and interpolation | `@angee/ui` owns the shared native initializer and provider-less defaults; `@angee/app` owns the composed active instance and provider; addons contribute namespace-relative bundles |
| date-fns | Date and relative-time formatting | Date and timestamp widgets |
| @number-flow/react | Animated numeric text, Intl formatting and reduced-motion-aware transitions | `MetricTile` provides the typed numeric path, binds shared motion tokens and keeps animation opt-in |
| use-debounce | Debounced React values and callbacks | Search and filter inputs |
| Tailwind 4 | Token styling engine | Semantic token set |
| tailwind-merge | Safe class merging | `cn()` helper |
| lucide-react | Icons | Name-referenced icon registry |
| Vite | Bundling, dev server, HMR | Project integration |
| @sentry/react >= 11.4 | Browser error capture and the React 19 root error handler (`reactErrorHandler`) | `bootApp` takes `errorReporting`; [`startErrorReporting`](../packages/app/src/error-reporting.ts) imports the SDK only for a non-empty DSN (`VITE_SENTRY_DSN`, stack input `sentry_web_dsn`) and gives the app root uncaught, caught and recoverable handlers that keep React's console output |
| @agentclientprotocol/sdk | ACP v1/v2 clients — JSON-RPC sessions, list/load/resume, prompt/cancel, permissions and session updates | [One chat runtime](../src/angee/agents/web/src/useAcpRuntime.ts) selects the SDK client from the endpoint's protocol version; v2 uses the SDK's `experimental` surface, pinned together with the Python SDK. [WebSocket transport](../src/angee/agents/web/src/acp-transport.ts) composes the SDK stream with routed tokens or same-origin cookies |
| @assistant-ui/react | Chat thread UI — message store, composer, tool-call rendering | ACP-streaming runtime adapter and styled thread surface |
| streamdown | Streamed-markdown render for assistant chunks | Assistant message body in the agent chat |
| @noble/hashes | Portable audited SHA-256 for browser file bytes, including non-secure LAN HTTP origins where WebCrypto digest is unavailable | `@angee/storage` emits the lowercase content hash used by upload deduplication and finalize verification |
| react-pdf (+ pdfjs-dist) | Inline PDF rendering (pdf.js) | storage file previewer |
| @vidstack/react | Inline video/audio player | storage file previewer |
| heic-to | Client-side HEIC/HEIF decode to a displayable image (current libheif-wasm) | storage HEIC previewer |
| pnpm | JavaScript dependency resolution and workspaces | Workspace layout |
| Node >= 22.13 | JavaScript build runtime | Project runtime |

Chat-UI library choice: `@assistant-ui/react` owns the chat-UX surface (composed
over ACP); CopilotKit and `@headlessui/react` were evaluated and rejected, and
TanStack AI is a watch item.
The agents fragment owns shared chat presentation and ACP transport.

## Hasura Dialect Rule

`strawberry-django-hasura`, the operator daemon SDL, `@refinedev/hasura`, and
Angee's refine/data glue share one Hasura-default wire contract. Because the
daemon already speaks this contract, the operator web package consumes it as a
Refine `operator` data provider (bearer-authed `createAngeeHasuraDataProvider`)
for request/response, the same shape as the `console`/`public` providers; only
its live subscriptions and the raw log socket stay on the daemon ws transport.
Grouped
resources must keep the DDN/NDC-preview shape:
`<resource>_groups(group_by, where, having, order_by, limit, offset): [<resource>_group!]!`,
with each group returning a typed `key: <Model>GroupKey!` and the free
`aggregate: <Model>Aggregate!`. The stock `<resource>_aggregate` root remains
the unmodified refine/Hasura aggregate surface; grouped roots are authored
operations owned by the dialect adapters.

Future grouped features such as bucket ordering, bucket predicates, additional
date extraction, or JSON drill-down operators must be added at the dialect
owners together: the Django adapter, operator SDL, emitted resource metadata,
and the refine authored-operation helpers. Do not add frontend-only group
semantics or local provider dialects.

Named upstream gap: `strawberry-django-hasura` needs native `filter_expressions`
for model resources with Django-owned computed predicates. Until that API exists,
[`hasura_model_resource`](../src/angee/graphql/data/hasura.py) bridges native
input extensions into the dependency's dataclass filter visitor and scalar
comparison types. That bridge relies on upstream internals; move it upstream
and delete the local adapter when the dependency owns computed model filters.
An addon composing onto another addon's model contributes such a filter through
the same bridge by declaring `hasura_filter_expressions` (name → provider taking
the target queryset) on its extension base, so the owner's resource never names
it; intake's `requester_name` and `requested_by_viewer` on `projects.Task` are
the example. A provider owns its access rule: filter only on what every reader
of the row already sees, or on the actor alone.

## Rendered Binding

Angee's frontend is Refine-native: the app composes one `<Refine>` root, resource
metadata projects into refine resources, and the rendered binding owns only
domain presentation over native TanStack and RHF state, with Refine core owning
resource transport/auth/live integration. The active frontend owners are
`@angee/app`, `@angee/refine`, `@angee/metadata`, and `@angee/ui`.

| Pick | Owns | Angee adds |
|---|---|---|
| @base-ui/react | Headless primitives: dialog, popover, menu, tabs, tooltip, field, toolbar, combobox, select, scroll area, and related UI | Styled binding and composition rules; controlled `open`/`onOpenChange` owns popover/dialog transition timing |
| @floating-ui/react-dom | Floating-element positioning and virtual anchors | Popover and menu anchoring |
| @angee/logo-react | Angee brand logo and cube marks | Brand lockup in the public layout |
| react-markdown + remark-gfm | Markdown rendering (GitHub-flavored) | Markdown widget preview |
| tailwind-variants | Variant recipes with slots | Component recipes |
| tw-animate-css | Tailwind 4 animation utilities | Motion tokens |
| cmdk | Command menu | Spotlight command surface |
| react-day-picker | Calendar | Date widgets |
| react-resizable-panels | Split panes | Layout and inspector panes |
| CodeMirror 6 (+ @codemirror/lang-json) | Text / Markdown / JSON editor | Markdown and JSON widget editors (shared `useCodeMirrorEditor`) |
| @xyflow/react | node/edge graph canvas | `@angee/ui` [GraphView](../packages/ui/src/views/GraphView.tsx) canvas and [GraphEditor](../packages/ui/src/views/GraphEditor.tsx) editing controls |
| @dagrejs/dagre | directed-graph layout | `@angee/ui` [layoutGraph and placeGraphNodeBeside](../packages/ui/src/views/graph-layout.ts) placement helpers |
| FullCalendar (Standard: `@fullcalendar/react` + `@fullcalendar/daygrid` + `@fullcalendar/timegrid` + `@fullcalendar/interaction`) | Month/week/day event calendar, drag/resize/select | `@angee/ui` `CalendarView` renders server-expanded occurrences and wires interactions to auto-CRUD; code-split behind a lazy import and themed through the token set |
| ReUI Base UI Gantt (vendored MIT) + @date-fns/tz | Date-scaled rows, bar layout, zoom, timeline navigation and timezone arithmetic | `@angee/ui` GanttView lazy-loads the drawing layer; the `gantt` collection kind composes native list queries and shared resource filter/search/group/saved-view state, pages related rows with all their bars, preserves empty rows, and disables drag/resize writes. Source, local adaptations and license: `packages/ui/src/views/gantt/UPSTREAM.md` and `LICENSE`. |
| @dnd-kit | Drag and drop | Board and rail interactions |
| Native browser drag/drop | File drag enter/leave/drop events and `DataTransfer.files` | `@angee/ui` upload drop target primitive |

Event-calendar library choice: FullCalendar Standard (all four packages MIT)
owns the month/week/day event surface. `@schedule-x` was evaluated and rejected
because its drag-and-drop and drag-to-create plugins — exactly the two
interactions a framework calendar primitive requires — are premium/paid, a
license cliff on code every consumer inherits (revisit only if it relicenses).
FullCalendar's commercial `@fullcalendar/resource-*` views stay out, a separate
license decision at the W4/W5 resource calendars. Distinct from `react-day-picker`
above, which owns the date-picker day grid behind date widgets, not an event
calendar.

## Tooling

| Pick | Owns | Angee adds |
|---|---|---|
| Cobra | `angee` CLI implementation | Dev supervisor, init, workspaces, templates; stack lifecycle requires native operator job holds and isolated dependency jobs. The [reusable workflow](../.github/workflows/reusable-checks.yml) owns the required release in its `angee-version` default |
| hatchling | Native Python distribution metadata and wheel/editable/sdist build lifecycle | Addon distributions compose the published hatch-angee defaults. Composition is a separate native job. Packaging does not provision databases |
| hatch-angee >= 1.1, < 2 | In-place addon layout and metadata defaults over Hatchling; native PEP 517 wheel/editable/sdist hooks | One addon entry point determines package layout; the [upstream authoring contract](https://github.com/ang-ee/hatch-angee#readme) defines opt-in entry-point generation and direct-reference defaults. Explicit Hatch configuration wins. The published backend owns no dependency graph, marketplace taxonomy or runtime hooks |
| build[uv] | Native PEP 517 wheel builds and isolated build environments | The [VCS catalogue](../src/angee/platform_integrate_vcs/catalog.py) prepares source metadata without loading the addon into the serving process; the operator owns complete remote checkouts |
| ruff | Python lint and format | Repo checks |
| angee-mypy + angee-django-stubs[compatible-mypy] | Python and Django ORM type checking, model descriptors, manager factories, generic constructors, and TextChoices enums | Published Angee distributions pinned by the dev manifest preserve native manager contracts and relation identities without installing competing upstream distributions; their native modules remain `mypy`, `mypy_django_plugin` and `django_stubs_ext`; native compiler and `angee.provenance.typing` plugins compose through mypy's plugin list; the native Django plugin consumes the declared model registry in [`tests.typing_settings`](../tests/typing_settings.py), isolated from the live stack and pytest |
| pytest + pytest-django + pytest-xdist + pytest-randomly | Backend tests, native test-database setup and transactional flush, worker isolation, and opt-in randomized ordering | Synthetic projects, addon-owned [`workflows`](../src/angee/workflows/testing/__init__.py) and [`integrate`](../src/angee/integrate/testing/__init__.py) test compositions, central [`decisions`](../src/angee/decisions/testing/models.py) model registration; generic [`composed_tables`](../src/angee/base/testing/fixtures.py) permission synchronization; CI schedules SQLite tests by file |
| pytest-split | Partitioning collected tests into disjoint duration-balanced shards | Framework SQLite/PostgreSQL and consumer CI shards, plus the framework's committed [`.test_durations`](../.test_durations); [Checks](checks.md#what-ci-actually-runs) owns scheduling and when to refresh timings |
| Faker | Test and seed data generation | Bulk lorem fixtures (e.g. `seed_lorem_notes`) |
| Vitest | TypeScript and React tests | Frontend unit checks; optional peer for `@angee/refine/testing`, which owns provider spies, and the `@angee/app/vitest` config entry. Tests declare direct workspace dependencies for fixture owners such as `@angee/metadata/testing`. |
| happy-dom | DOM environment for Vitest | Per-file env opt-in for hook and component tests |
| @testing-library/react | React component and hook test rendering | Provider-wrapped render and hook harnesses |
| Playwright | Browser tests | `@angee/e2e` harness: workspace-isolated runner, role `storageState` login, GraphQL `api` fixture, Page Object base ([E2E guide](frontend/e2e.md)) |
| @playwright/mcp | Interactive browser-driving for host coding agents | Repo-root `.mcp.json` server (npx-run, pinned), using Playwright MCP's native per-project persistent profile in the OS cache; source checkouts own no browser state. The agent navigates to the stack's configured `ANGEE_UI_PORT`. Container MCP services keep their declared stack-owned profile mount. Distinct from `@angee/e2e` (the deterministic test runner) and `agents.MCPServer` (the MCP config rendered for operator-provisioned product agents) |
| Storybook | Component workshop | `@angee/ui` and addon previews |
| GitHub Actions | CI | Implemented build/type/test lanes and repository policies; [Checks](checks.md#what-ci-actually-runs) distinguishes local requirements and manual browser verification |
| Copier | Project and addon templates | Angee templates |

## Proposed, Not Locked

| Pick | Role |
|---|---|
| Yjs + Hocuspocus | Collaborative editing |
| pgvector / sqlite-vec / python-igraph / lightrag-hku | Vector search and graph RAG |
| django-ninja | Typed REST sidecars (callbacks, webhooks, health) — over the locked pydantic |
| boto3 | S3-compatible storage backend (S3 / R2 / MinIO presigned IO) |
| ansi-to-react | ANSI log panels |
| simple-icons + @lobehub/icons | Brand and vendor SVG icon registry |

## Change Policy

- Add a dependency only with an owner row here.
- Remove a dependency by deleting its row.
- Swap a dependency by updating the row and explaining why in the change.
- Move proposed picks into a locked section before shipping code that depends on
  them.
