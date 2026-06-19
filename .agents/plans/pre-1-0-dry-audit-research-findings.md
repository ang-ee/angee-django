# Pre-1.0 DRY Audit Research Findings

**Parent plan:** `.agents/plans/view-composition-drift-audit.md`
**Workflow:** `.agents/plans/refactoring-workflow.md`
**Slicing:** `.agents/plans/reviewer-slicing-strategy.md`

This file consolidates the first parallel research batch. It is not an
implementation plan yet. It is the decision queue and finding inventory that
should feed drawing-board review, architect escalation, and narrow
implementation slices.

No product code was changed by the researchers. Tests were not run by the
researchers; they only searched, read, and reported.

## Batch Coverage

- [x] Backend framework owners: `angee.base`, `angee.graphql`, `angee.compose`
  and seam tests.
- [x] Frontend framework/package owners: `@angee/base`, `@angee/sdk`, and
  representative addon web call sites.
- [x] Integration addons: `integrate`, `integrate_github`,
  `iam_integrate_oidc`.
- [x] Agents addons: `agents`, `agents_integrate_anthropic`,
  `agents_integrate_openai`, and agents web.
- [x] IAM/resources/storage/knowledge.
- [x] Operator/platform/MCP.
- [x] Cross-cutting naming and decomposition.

## Architect Decision Queue

These findings are large enough that implementation should stop and get human
architect direction before preserving the current shape.

### Native GraphQL Extension Owner

- **Status:** Done, verified 2026-06-19. Output type extensions now use native
  Strawberry/strawberry-django `extend=True`; Angee only keeps schema-bucket
  collection, addon-order dedupe, and registration with Strawberry.
- **Finding:** Angee manually merges output GraphQL type extensions while
  `strawberry_django.type(..., extend=True)` exists.
- **Current code:** `angee/graphql/schema.py`, `angee/graphql/extension.py`,
  `@extends_type`, `type_extensions`.
- **Greenfield:** output type extension uses native strawberry-django extension;
  Angee keeps input extension, addon ordering, and collision policy only.
- **True owner:** `strawberry-django` for output type extension; `angee.graphql`
  for input extension/order/collision semantics.
- **Deletion:** shrink or delete output-side `extends_type`,
  `_ensure_type_extensions_applied`, `_apply_type_extension`, and
  `_merge_extension_fields` if parity holds.
- **Guardrail:** parity tests across named schemas, downstream addons,
  collisions, and idempotency.
- **Evidence:** no source hits remain for `extends_type`,
  `_ensure_type_extensions_applied`, `_apply_type_extension`, or
  `_merge_extension_fields`; `type_extensions` now carries native extension
  classes such as `@strawberry_django.type(..., extend=True)` in
  `addons/angee/agents/schema.py` and `addons/angee/iam_integrate_oidc/schema.py`.
- **Verification:** independent reviewer confirmed the old output merge path is
  gone; `uv run pytest tests/test_graphql.py` passed with 26 tests.

### CRUD Update Mutation Owner

- **Finding:** `_AngeeUpdateMutation` subclasses and partially copies
  strawberry-django internals to alter target lookup for REBAC/write scope.
- **Greenfield:** `crud()` is a thin wrapper around
  `strawberry_django.mutations.create/update`; Angee adds delete preview and
  naming only.
- **True owner:** `strawberry-django` for mutation mechanics;
  `django-zed-rebac` for read/write/field-redaction semantics.
- **Deletion:** remove custom update mutation internals if a library hook or
  upstream patch can provide write-queryset/target lookup.
- **Guardrail:** regression for updating a field-gated REBAC model where read
  redaction must not block write target lookup.

### OAuth/OIDC Protocol Owner

- **Finding:** Angee hand-owns OAuth authorize/token/refresh/revoke/userinfo
  mechanics. `docs/stack.md` has PyJWT for JWT/JOSE but no OAuth client owner.
- **Greenfield:** `OAuthClient` stores config and policy; a library such as
  Authlib performs OAuth/OIDC flow; Angee owns state, persistence, credentials,
  REBAC, and UI payloads.
- **True owner:** likely Authlib for OAuth/OIDC protocol if accepted; PyJWT/OIDC
  Core for ID token validation.
- **Deletion:** shrink `OAuthClientProtocol` and OIDC protocol glue to row
  projection/result adaptation.
- **Guardrail:** parity tests for manual redirect, PKCE, nonce, honest
  User-Agent, refresh rotation, userinfo enrichment, and provider error shaping.

### Inference Provider Creation Owner

- **Finding:** standalone `createInferenceProvider` repeats integration impl
  checks and related-row creation.
- **Locked direction:** integration kinds use Django multi-table inheritance
  child models. `Integration` is the shared parent connection row;
  `InferenceProvider` is the concrete child row. Provider SDK/protocol choice
  lives on `InferenceProvider.backend_class` as an `ImplClassField`.
- **Dependency decision:** run an early spike on `django-polymorphic` as the
  parent-query/downcasting owner. Add it to `docs/stack.md` and the dependency
  graph only if it works cleanly with Angee emitted runtime models, REBAC
  managers, strawberry-django, resources, migrations, and aggregate/list queries.
- **Greenfield:** create the concrete inference integration child once. OpenAI,
  Anthropic, manual, DeepSeek, and other adapters are backend-class values with
  default-bearing/inheritable impl classes, not `Integration.impl_class` values.
- **True owner:** Django multi-table inheritance for persisted parent/child
  shape; `django-polymorphic` only if the spike proves it should own
  parent-to-child query behavior; `InferenceProvider` for provider-specific
  config, refresh, service env, model catalogue, and `backend_class`.
- **Deletion:** delete or demote `InferenceProviderInput` and
  `createInferenceProvider`; delete `IntegrationImpl.related_model` and related
  row creation helpers when child creation owns the flow.
- **Guardrail:** tests for concrete child creation through the integration
  surface, backend defaults/materialization, parent listing, child detail routing,
  permissions, and SDL.

### Inference Model Alias Rows

- **Finding:** broker-prefixed aliases are materialized as duplicate catalogue
  rows, so runtime handle convention leaks into provider-native model catalogue.
- **Greenfield:** one native `InferenceModel` row per provider model; runtime or
  template/agent handle binding derives the broker-specific selector.
- **True owner:** provider SDK for native catalogue; `InferenceModel` for
  persisted selection; agent runtime/service template for handle convention.
- **Deletion:** remove native + broker duplicate rows after migration.
- **Guardrail:** migration/selection tests before deleting alias rows.

### Agent Identity And MCP Tool Authorization

- **Finding:** MCP bearer credential owner becomes the actor; TODOs remain for
  distinct agent identity and per-tool authorization.
- **Greenfield:** ephemeral ACP sessions; distinct `agents/agent` actor subject
  with explicit grants; MCP tools discovered from server metadata.
- **True owner:** ACP/assistant-ui for thread runtime; IAM/REBAC for actor
  identity; MCP/FastMCP for tool schema/transport.
- **Deletion:** avoid message/run persistence until product need; later delete
  borrowed-user MCP identity and possibly manual MCP tool CRUD.
- **Guardrail:** distinct-agent-subject and per-tool-denial tests.

### MCP GraphQL Tool Bridge

- **Finding:** MCP bridge hand-compiles JSON Schema, GraphQL documents, argument
  projection, scalar mapping, and result shaping.
- **Greenfield:** GraphQL exports operation/tool contracts; MCP only registers
  them with FastMCP.
- **True owner:** `angee.graphql` for operation semantics;
  FastMCP/MCP ecosystem for transport/schema.
- **Deletion:** delete bespoke compiler pieces if contracts move to
  `angee.graphql` or a library-owned GraphQL-to-MCP bridge.
- **Guardrail:** compiler tests for enums, nested inputs, defaults, non-null,
  unknown fields, SQID, aliases/errors, and advertised schema shape.

### Frontend I18n Runtime

- **Finding:** `docs/stack.md` names i18next as runtime owner, but SDK currently
  owns custom lookup/interpolation/fallback logic and base still has hardcoded
  shared strings.
- **Greenfield:** addon manifests provide namespace resources; i18next owns
  lookup, interpolation, fallback, and pluralization; Angee hooks are thin.
- **True owner:** i18next; `@angee/sdk` composition seam; `@angee/base` string
  bundles.
- **Deletion:** delete custom interpolation/fallback after wiring i18next.
- **Guardrail:** runtime i18n tests, translated Form/List smoke, hardcoded
  shared-string search.

### SDK GraphQL Transport For Operator

- **Finding:** operator duplicates `subscriptionExchange`, retry/fatal close
  logic, and direct query/mutation wrappers because SDK cannot pass WebSocket
  auth params.
- **Greenfield:** operator provides endpoint/token facts; SDK builds HTTP + WS
  clients with auth/subscription params.
- **True owner:** urql/graphql-ws externally; `@angee/sdk` transport seam
  internally.
- **Deletion:** expose SDK `wsConnectionParams` or subscription auth options,
  then delete operator client fork.
- **Guardrail:** SDK client tests for WS params/retry behavior; grep guard for
  direct `graphql-ws` / `subscriptionExchange` outside SDK.

### Data View Row Mechanics

- **Finding:** filtering, lookup matching, sorting, grouping, selection, and
  pagination are partly Angee-owned even though stack names TanStack
  Table/Virtual for much of this.
- **Greenfield:** one data-view adapter: server-backed mode delegates to
  GraphQL; row-backed mode delegates sort/filter/group/select/virtualization to
  TanStack where native behavior fits.
- **True owner:** TanStack Table/Virtual externally; `@angee/base` owns
  URL/search and Angee declarations.
- **Deletion:** consolidate toolbar/state adapter and push row-backed transforms
  into controlled TanStack row models.
- **Guardrail:** List/Rows sorting, filtering, grouping, selection, visibility,
  and virtualization browser smoke.

### Storage/Knowledge Explorer Shape

- **Finding:** storage and knowledge both load capped catalogues, scope/filter
  client-side, manage route-open state, and render tree/list/detail workflows.
- **Greenfield:** GraphQL owns scoped/paginated hierarchy; base explorer owns
  selector/tree/content/aside; addons supply row projection/actions.
- **True owner:** likely `angee.graphql` plus `@angee/base` explorer/tree
  primitives.
- **Deletion:** introduce backend-owned scoped queries and a base explorer
  primitive; delete local capped catalogue workflows.
- **Guardrail:** scope switching, route-open, drag/drop, large hierarchy smoke;
  guard against new `*_LIST_LIMIT = 500` caps.

### VCS Naming Decision

- **Finding:** one row is named both `VcsBridge` and `VCS Integration` across
  model, GraphQL roots, REBAC/resource type, routes, and labels.
- **Greenfield:** choose `VcsBridge` or `VcsIntegration` once.
- **True owner:** public model/schema vocabulary.
- **Deletion:** remove the other noun from routes, GraphQL roots, resource
  names, tests, and docs/comments.
- **Guardrail:** schema check, GraphQL action tests, route/menu smoke, naming
  test that model/type/root/action share a noun.

### Canonical Frontend Model Key

- **Finding:** frontend accepts both bare model names and dotted labels
  (`Integration` vs `integrate.Integration`). Route indexing stores raw keys.
- **Greenfield:** one canonical model key owned by SDK model metadata/selection.
- **True owner:** `@angee/sdk` model metadata/selection contract.
- **Deletion:** centralize or remove aliasing.
- **Guardrail:** relation-follow tests and lint/search guard for disallowed
  alias form.

### Template Manifest Owner

- **Finding:** `_angee` manifest parsing is under integrate VCS, while agents
  reuse it and operator independently resolves template refs.
- **Greenfield:** one template manifest descriptor owns `_angee.kind/name/inputs`;
  integrate stores discovered rows only.
- **True owner:** likely operator/template subsystem or small framework template
  primitive; PyYAML owns YAML parsing.
- **Deletion:** centralize manifest parsing once and delete independent copies.
- **Guardrail:** integrate VCS tests, operator template discovery tests, nested
  Copier template fixture with deterministic input ordering.

### Knowledge Source/Templates Boundary

- **Finding:** knowledge declares resources but omits `angee.resources`
  dependency; frontend owns some wikilink/cycle behavior; `Page.Kind.TEMPLATE`
  overlaps with integrate Copier `Template`.
- **Greenfield:** knowledge owns vault/page/markdown/link semantics only; source
  sync lives in integrate or bridge addons; file-backed knowledge uses storage;
  demo seed data declares resources dependency.
- **True owner:** knowledge model/manager for page invariants and wikilinks;
  integrate for VCS/template source sync; storage for files; resources for
  manifests.
- **Deletion:** move frontend-only invariants/resolution to backend owners and
  avoid knowledge-local repo/source sync.
- **Guardrail:** backend parent-cycle/cross-vault/wikilink case tests and
  composition test with knowledge resources installed.

## No-Architect Or Lower-Risk Deletion Candidates

These can likely become implementation slices after normal review, unless the
first implementation uncovers API/boundary changes.

- **Storage file drop:** replace storage-local drag state/handlers/overlay with
  `@angee/base` `UploadDropTarget`.
- **OAuth account linking:** move duplicate OIDC/connect account+credential
  persistence into one integrate-owned manager/service.
- **SSRF-safe HTTP:** consolidate GitHub and webhook outbound safety into a
  pinned transport in `integrate.net` or a model-free transport owner.
- **Manual OAuth UX:** extract shared manual `code#state` prompt/parsing/complete
  flow into integrate web connect module.
- **Agent provisioning workflow:** move render plan, secret sync, provision,
  reprovision, and deprovision orchestration out of `schema.py` into an
  addon-local provisioner service.
- **Operator action UI:** consolidate repeated action descriptors, busy
  aggregation, confirm/toast, and refetch wiring into an operator web action
  helper.
- **Platform row projector:** centralize platform reverse-edge/index facts in
  one web projector or backend projection; replace manual href parsing with
  structured route targets.
- **IAM schema resource picker:** replace manual listbox refs/roles/keyboard
  logic with Base UI select or a base `SelectableList` primitive.
- **Impl defaults:** move default materialization from `ImplDefaultsMixin.save`
  probing into `ImplClassField`.
- **Provider demo resource chain:** wait until a third provider before extracting
  a provider-addon seed/manifest helper; with two providers, duplication is not
  yet enough to justify abstraction.

## Upstream Or Dependency Owner Candidates

These are not automatically local work. The plan's "no owner is out of reach"
rule applies.

- **strawberry-django:** native output type extension parity; update mutation
  write-target/write-queryset hook; revision field typing projection.
- **django-zed-rebac:** public visible-queryset/read-scope helper; inverse
  relationship lookup; role/ref metadata projection for IAM.
- **django-sqids:** nullable-join `from_db_value(None)` and public encode helper.
- **django-yamlconf:** strict parse/reference failure mode and no-ancestor
  cascade option.
- **OAuth/OIDC library:** Authlib or another locked OAuth client owner.
- **ACP client:** remove local `setSessionModel` wire-method shim when dependency
  supports it.
- **GraphQL/MCP ecosystem:** GraphQL operation contract to MCP tool schema if a
  library can own it.

## Cross-Cutting Naming And Decomposition Findings

- **`Provider` means two concepts:** agents `InferenceProvider` and integrate
  `OAuthClient`/OAuth provider routes both use generic provider language.
- **`VcsBridge` vs `VCS Integration`:** one public concept has two nouns.
- **Source-kind pages repeat:** integrate sources, agents skill sources, and
  templates repeat source-kind page mechanics.
- **Agent lifecycle rules are page-local:** React decodes raw status/runtime
  strings instead of consuming model/schema-owned availability.
- **OAuth readiness is page-local:** connect provider page rechecks endpoint and
  client fields despite `OAuthClient.configuration_state`.
- **Page files hide owners:** files named after menu bundles export multiple
  owner-specific pages (`AgentsPage` plus templates, `InferencePage` plus
  providers/models, `McpPage` plus servers/tools).
- **Agent context preview is heuristic:** generic `_meta`/field inspection should
  become declared preview/context projection.

## Green Patterns To Preserve

- `ImplClassField` remains a clean owner placement for backend/adapter choice on
  the concrete row. `IntegrationImpl.related_model` is superseded by the locked
  Django child-model direction and should not be preserved as a green pattern.
- `rebac_aggregate_builder` is thin useful glue around
  `strawberry-django-aggregates` plus REBAC policy.
- MCP server lifecycle and ASGI mount composition are broadly healthy:
  app declares tools, Angee discovers, FastMCP serves.
- Storage backend protocol correctly wraps Django storage concepts.
- Agents provider SDK backends already share `SDKInferenceBackend`; vendor
  differences are mostly real.
- Glyph registry and CodeMirror editor ownership look healthy.
- `AddRepositoryControl` remains a valid documented server-backed typeahead
  exception.

## Suggested Next Review Slices

Run these before implementation:

1. [x] `angee.graphql / output type extension / native strawberry-django extend=True`
2. `angee.graphql / CRUD update / upstream hook vs custom mutation`
3. `integrate / OAuth protocol / Authlib stack decision`
4. `agents+integrate / integration child models / django-polymorphic spike`
5. `agents / inference model catalogue / broker handle binding`
6. `@angee/sdk / operator GraphQL transport / WS auth options`
7. `@angee/base / i18n / i18next runtime wiring`
8. `@angee/base / data-view row mechanics / TanStack delegation`
9. `storage+knowledge / explorer shape / backend scoped query + base explorer`
10. `integrate / source-kind pages / shared source page primitive`
11. `integrate / VCS naming / bridge vs integration`
12. `agents / lifecycle action availability / model/schema owner`
13. `IAM / REBAC role-ref projection / upstream vs Angee projection`
14. `MCP / GraphQL tool bridge / operation contracts`

## Suggested Implementation Order After Decisions

1. Low-risk deletions that prove the process:
   - storage `UploadDropTarget`
   - OAuth account-link persistence extraction
   - manual OAuth UX connect helper
   - operator action UI helper
2. Owner-level framework decisions:
   - GraphQL extension/update mutation
   - SDK transport
   - i18next runtime
   - data-view/TanStack row mechanics
3. Public vocabulary/API decisions:
   - VCS bridge/integration
   - provider/OAuth client route names
   - frontend model key canonicalization
4. Cross-addon architecture decisions:
   - source-kind pages
   - storage/knowledge explorer
   - template manifest owner
   - agent context preview seam

Every implementation slice must return to `.agents/plans/refactoring-workflow.md`
and record accepted/rejected alternatives before coding.
