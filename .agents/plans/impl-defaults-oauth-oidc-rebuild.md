# Implementation classes with defaults — framework feature + OAuth/OIDC + integrations rebuild (clean slate)

## Context

We have three impl-class registries today (`ANGEE_INTEGRATION_IMPLS`, the VCS/inference backends) resolved by `ImplClassField` (`angee/base/fields.py:165`), but an impl class carries only *behavior* — choosing one brings no configuration. The maintainer wants one framework primitive: **a registered, inheritable, default-bearing implementation class** — pick "Google" (or "Generic OIDC", "GmailIMAP") and its default endpoints/scopes/config materialize onto the row, editable. The same primitive serves OAuth provider types **and** integration/bridge impls, with inheritance (`GmailIMAP(IMAPBridge)` inherits IMAP defaults). Alongside: a framework name→slug machinery (stop hand-typing `slug`), and folding OIDC into `OAuthClient` via Angee's `extends` (one model, no `OidcClient`, no 1:1, no broken cross-model discovery).

This is **zero backward compat / clean slate** — we rebuild the just-landed `IntegrationImpl` and integrations create-form onto the new primitive, drop `OidcClient`, and reset the dev DB (no data migrations). Reference: authentik's `SourceType` registry (`authentik/sources/oauth/types/registry.py`) materializes per-type defaults onto one model — the proven shape.

---

## Part 1 — Framework: `ImplBase` (default-bearing, inheritable) + `ImplClassField` defaults

New `angee/base/impl.py`:
```python
class ImplBase:
    key: ClassVar[str] = ""          # registry key; "" = abstract base (unregistered)
    label: ClassVar[str] = ""
    icon: ClassVar[str] = ""
    category: ClassVar[str] = ""
    defaults: ClassVar[dict[str, Any]] = {}   # field values this impl seeds (scalars/JSON; FKs as natural keys)

    @classmethod
    def effective_defaults(cls) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for base in reversed(cls.__mro__):       # base → derived; derived wins
            merged.update(getattr(base, "defaults", {}))
        return merged

    @classmethod
    def materialize(cls, instance, *, overwrite_blank_only=True) -> None:
        """Copy effective defaults onto the instance's unset fields (FK natural keys resolved by slug)."""
```
- Abstract bases (`IMAPBridge`, `GenericOidc`'s `OAuthProviderType` root) set no `key` and stay out of the registry; concrete leaves (incl. the `Generic*` fallbacks) carry a `key` and are pickable.
- Extend `ImplClassField` (`angee/base/fields.py`): keep key→class resolution + the choices enum it already builds; add `impl_choices()` → `[{key,label,icon,category,defaults}]` for the registry, and `resolve_impl(key)` typed to `ImplBase`.
- Model seeding: an `ImplDefaultsMixin` (or `ImplClassField`-driven hook) runs `impl.materialize(self)` on create, so API/seed creation gets defaults too (FK defaults resolved by natural key; missing target → left blank).
- GraphQL: a generic `impl_choices(model, field)` resolver exposing the choices + `defaults` JSON so forms can prefill (one resolver, reused by every `ImplClassField`).

## Part 2 — Framework: form prefill on impl select (`@angee/base`)

New form behavior: an `ImplClassField` rendered as a picker that, on change, **materializes** the chosen impl's `defaults` into the dependent fields (editable, then saved) — the frontend half of "choosing an impl brings its defaults." Driven by the `impl_choices` query (Part 1). Implement as a widget/behavior (`implSelect`) wired to TanStack Form `setValue` for each default key; reusable by any model with an `ImplClassField`. (This is the one genuinely new form primitive; everything else composes existing DSL — `Group`/`Tab`/`showWhen`.)

## Part 3 — Framework: `SlugFromNameMixin` (`angee/base/mixins.py`)

name→slug auto-gen mirroring `AuditMixin.save()` (`angee/base/mixins.py:103`): class attrs `slug_source_field` (`"display_name"`), `slug_scope_fields` (uniqueness scope); slugify on blank, ensure uniqueness within scope (`-2`/`-3`…), DB constraint stays source of truth. Slug columns become `blank=True`; inputs become `strawberry.UNSET`; create forms drop the slug field. Adopt on `OAuthClient` (scope `("environment",)`) + `Vendor` (global); others opt in.

## Part 4 — OAuth/OIDC: one model, provider types, OIDC via `extends`

- **Drop `OidcClient`** (model, `OidcClientType`, the 1:1, its CRUD, the `extends_type` projection). `iam_integrate_oidc` instead declares an Angee model extension (first real use of `extends`, composer support at `angee/compose/runtime.py:456`):
  ```python
  class OAuthClientOidc(AngeeModel):
      extends = "integrate.OAuthClient"
      issuer = ...; jwks_uri = ...
      login_enabled = models.BooleanField(default=False)   # the login-provider discriminator
      link_on_email_match = ...; create_on_login = ...; allowed_email_domains = ...
  ```
  integrate never declares these; the OIDC addon contributes them at build time → one `OAuthClient` table, pure base, login fields present only when the addon is composed.
- **`provider_type = ImplClassField(base_class=OAuthProviderType, registry="ANGEE_OAUTH_PROVIDER_TYPES")`** on `OAuthClient`. `discovery_url` moves onto `OAuthClient`; discovery *fetch* (the cached `_discovery_document`/`_get_json`, today `iam_integrate_oidc/protocol.py:199`) moves into `integrate/oauth/` (OAuth-generic, RFC 8414); `OAuthClient.discover_endpoints()` fills its own endpoints (reusing the existing `fill_endpoints_from_discovery`).
- Provider types (shared registry, dotted-key autoconfig):
  - `integrate`: `GenericOAuth2` (pickable fallback, no endpoint defaults).
  - `iam_integrate_oidc`: `GenericOidc` (pickable fallback, `urls_customizable`, sets `login_enabled` + `openid` scope defaults, discovery-driven) and `GoogleType(GenericOidc)` (defaults: discovery URL, scopes, icon).
- **One form** (`ProvidersPage.tsx`): `displayName`→slug, `provider_type` picker (prefills endpoints/scopes/icon via Part 2), `discoveryUrl` + a **Discover endpoints** action, and an **OIDC/Login tab** (the extension fields) shown `showWhen` `login_enabled`. No separate OIDC form/model.
- Public login picker (`iam_integrate_oidc/schema.py`) filters `login_enabled=True` (was `oidc__isnull=False`).

## Part 5 — Integrations / bridges: refactor onto `ImplBase`, inheritable

- `IntegrationImpl(ImplBase)` (`addons/angee/integrate/impl.py`) — gains `defaults` + inheritance; keep `category`/`companion_model`. VCS/inference impls become default-bearing.
- Bridges: `BridgeImpl(IntegrationImpl)` base → `IMAPBridge(BridgeImpl)` (default `poll_interval`, sync behavior, companion) → `GmailIMAP(IMAPBridge)` (host/oauth-client/scopes defaults). `GenericIMAPBridge` pickable fallback.
- The integrations create form (just built) uses the Part-2 prefill: pick impl → config/companion fields materialize from `effective_defaults()`.

## Migration / blast radius (clean slate — dev DB reset)

- **Framework**: `angee/base/impl.py` (new), `angee/base/fields.py` (ImplClassField defaults), `angee/base/mixins.py` (SlugFromNameMixin), `@angee/base` FormView/widgets (impl-select prefill), a generic `impl_choices` GraphQL resolver.
- **integrate**: `OAuthClient` (+`provider_type`,`discovery_url`, slug blank, mixin), `impl.py` onto `ImplBase`, discovery fetch into `oauth/`, provider-type registry + `GenericOAuth2`, schema inputs/types, autoconfig.
- **iam_integrate_oidc**: delete `OidcClient`; add the `extends` model + `GenericOidc`/`GoogleType`; protocol/identity read `login_enabled` + `oauth_client.discovery_url`; schema (drop OidcClient CRUD, surface login fields on `OAuthClientType` natively); permissions.zed (drop `oidc_client` resource type — fields now on `oauth_client`); resource seeds.
- **Frontend**: `ProvidersPage.tsx` (provider-type picker + OIDC tab), delete `OidcProvidersPage.tsx`, `VendorsPage.tsx` (slug), integrations create form (prefill), documents/i18n.
- **Tests**: `tests/test_oidc.py` (rewrite around one model + provider types), `test_integrate*`, new `tests/test_impl.py` (ImplBase defaults/inheritance/materialize) + a slug-mixin test.
- No data migrations: regenerate runtime, `migrate` a fresh dev DB.

## Verification
- Backend: `angee build` → `makemigrations integrate iam_integrate_oidc` → fresh `migrate` → `rebac sync` → `resources load` → `schema --check`; `uv run pytest tests/test_impl.py tests/test_oidc.py tests/test_integrate*.py -q`.
- Frontend: `pnpm run typecheck && pnpm run test && pnpm run build`.
- End-to-end (`angee dev`): create an OAuth client → pick **Google** → confirm endpoints/scopes/icon prefill (editable) and slug derives from the name; toggle it a login provider via the OIDC tab. Create an integration → pick **GmailIMAP** → confirm IMAP+Gmail defaults materialize. Pick **Generic OIDC** → paste discovery URL → **Discover** fills the endpoints.
