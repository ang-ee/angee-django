# Plan: split federation out of `iam` → connection substrate into `integrate`, OIDC login into `iam_integrate_oidc`; make OAuth/OIDC clean and DRY

Status: proposed (awaiting go for implementation)
Approach: **zero backward-compat, zero deferral, zero tech debt.** Full migration
regen, rename rebac resource types, no compat shims, no dual-write.

## Why

`iam` today owns three concerns fused together:

1. **Core identity** — `User` (swappable), password login/logout, session, REBAC
   role admin. Inbound auth (`ModelBackend`, `RebacBackend`) never reads the OAuth
   models — verified.
2. **Connection substrate (outbound)** — `OAuthClient`, `ExternalAccount`,
   `Credential` (+ `CredentialKind`/handlers), `oidc/{client,state,errors}`. Every
   consumer lives *above* iam (`integrate.Integration.credential`,
   `agents.MCPServer.credential`, `vcs/backend`). `integrate` and `agents` reach
   **up** into iam for it — the find-the-owner smell.
3. **OIDC login** — `identity.py` resolver, `login_start/complete`,
   `link_account_*`, `available_connections`. Needs both identity *and* the OAuth
   client.

Because inbound auth never touches the substrate, the substrate can move down to
`integrate` (its true owner) with **no dependency cycle**. Login becomes a thin
third addon above both — it is *required* there, since putting it in either base
would create the cycle.

Second goal: the single `OAuthClient` model fuses OAuth-for-API config with
OIDC/login config behind an `is_oidc` flag (polymorphism smell) — dead OIDC
columns on every gemini/grok/anthropic row. Split it so OAuth is the base and
OIDC is a refinement, and so connecting an external OAuth account carries **zero
login logic**.

## Target topology

```
iam            ◄── integrate ◄── iam_integrate_oidc
(identity)         (connection      (OIDC login + link)
                    substrate)
agents         ──► integrate
example/notes  ──► iam, integrate, iam_integrate_oidc, ...
```

`Credential.user → AUTH_USER_MODEL` keeps `integrate → iam` (already true).
`iam` ends up with **zero** references to the substrate.

## OAuth/OIDC model redesign (the DRY core)

Replace the one flag-discriminated `OAuthClient` with a base + composition
extension. "Is this a login provider?" becomes *"does an `OidcClient` row exist?"*
— the `is_oidc` boolean is **deleted**.

### `integrate.OAuthClient` — OAuth base (connect-for-API, no login anything)
Fields: `slug`, `icon`, `environment`, `display_name`, `client_id`,
`client_secret`, `authorize_endpoint`, `token_endpoint`, `revoke_endpoint`,
`token_request_format`, `scopes_catalogue`, `default_scopes`, `supports_refresh`,
`refresh_rotates`, `supports_pkce`, `max_refresh_age_seconds`, `authorize_params`,
`token_params`, `manual_redirect_uri`, `is_enabled`.
Behavior: scope/param/redirect/token-format helpers, `resolve_connect_redirect`,
`configuration_state` (drop its discovery branch's OIDC assumption).
Used by: `connect_account_*` (any OAuth client), `Credential.oauth_client`.
**gemini/grok/anthropic = plain `OAuthClient` rows with no `OidcClient` — pure
connect, zero login logic.**

### `integrate.OidcClient` — OIDC protocol refinement (OneToOne → OAuthClient)
Fields: `oauth_client` (OneToOne, `related_name="oidc"`), `issuer`,
`discovery_url`, `jwks_uri`, `userinfo_endpoint`, `external_id_claim`,
`email_claim`, `display_name_claim`, `avatar_url_claim`.
Behavior: discovery, `verify_id_token`, `fetch_userinfo`, claim-extraction
(`external_id_from_claims`/`email_from_claims`/`display_name_from_claims`/
`avatar_url_from_claims`).
Co-query: connect picker = `OAuthClient.objects...`; login picker =
`OAuthClient.objects.filter(oidc__isnull=False)` / `OidcClient.objects...`.

### Login policy — owned by `iam_integrate_oidc` via model extension
`extends = "integrate.OidcClient"` contributing `link_on_email_match`,
`create_on_login`, `allowed_email_domains` (+ `allows_email_domain`). Lands on the
same table but **owned by the login addon** — connect/protocol never see it.

> Note: data-layer split is **composition** (OneToOne), because the composer
> flattens abstract bases into one concrete table per leaf (see `Bridge`/
> `VCSIntegration`) and does not use Django MTI — so "OIDC is-a OAuth" is
> expressed as inheritance at the **service layer** (below) and composition at the
> **data layer**. This kills the `is_oidc` flag, keeps both co-queryable, and
> keeps the OAuth base login-free.

## Service/code layer split (here OIDC inherits OAuth)

- `integrate/oauth/client.py` — base protocol: `build_authorize_url`,
  `exchange_code`, `refresh_token`, token-request shaping. Operates on
  `OAuthClient`.
- `integrate/oidc/client.py` — **extends** the OAuth client: adds discovery,
  `verify_id_token`, `fetch_userinfo`, claims. Operates on `OidcClient`.
- `integrate/oidc/state.py`, `errors.py` — move as-is.
- `Credential` refresh (`OAuthCredentialHandler.refresh`) calls the **OAuth** base
  client (refresh is OAuth, not OIDC) — drops its current `iam.oidc` import.

## Move inventory: `iam` → `integrate`

Models: `OAuthClient` (split as above), `ExternalAccount`, `Credential`
(+ `CredentialKind`, `CredentialStatus`, `AccountStatus`), credential handlers
(`credentials.py`). Protocol: `oidc/*` → `oauth/*` + `oidc/*`. GraphQL:
`OAuthClientType`, `ExternalAccountType`, `CredentialType` (+ CRUD/reveal/health),
`connect_account_*`, `unlink_account`, `my_connected_accounts`. Settings:
`ANGEE_IAM_OAUTH_CLIENTS`/`ANGEE_IAM_OIDC_*` → `ANGEE_INTEGRATE_*`. Command:
`oauth_clients` → integrate. Seed: `resources/install/010_iam.oauthclient.yaml` →
integrate. REBAC: `auth/oauth_client|external_account|credential` defs →
integrate, **renamed** `integrate/oauth_client|external_account|credential` (their
`owner: auth/user` relation still references iam — fine, integrate→iam).

`integrate` stops importing `CredentialType/ExternalAccountType` from `iam.schema`
(now owns them); keeps importing `UserType`, `session_user`,
`ADMIN_PERMISSION_CLASSES` from iam.

## New addon: `iam_integrate_oidc`

`addons/angee/iam_integrate_oidc/` → `name = "angee.iam_integrate_oidc"`,
`depends_on = ("angee.iam", "angee.integrate")`.
Owns: `identity.py` resolver (`resolve`/`link_user`/create-on-login/
link-on-email), `login_start`/`login_complete`, `link_account_*`,
`available_connections` (login picker over `OidcClient`), `_session_backend`
helper, the `OidcClient` login-policy model extension, login-policy rebac if any.
Contributes login mutations into the `public`/`console` buckets the composer
already merges across addons.

## `iam` after

`User`, `UserManager`, password `login`/`logout`, `current_user`,
`UserType`/`CurrentUserType`, REBAC role admin (roles/grants/schema/relationships),
`signals.py`, `BearerTokenCsrfExemptMiddleware`, `IAMUserMutation`,
`AUTH_USER_MODEL`. No OAuth/OIDC/credential anything.

## Consumer + test updates (zero compat)

- `agents/models.py`: `from angee.iam.credentials import CredentialKind` →
  `angee.integrate.credentials`; `iam.Credential` refs → `integrate.Credential`.
- `examples/notes-angee` demo seeds: `030_iam.oauth_client.yaml`,
  `080_iam.credential.yaml` → `integrate.oauth_client` / `integrate.credential`
  models; the `iam.OAuthClient` console import in `runtime/` regenerates.
- `tests/conftest.py`, `tests/test_connections.py`, `tests/test_oidc.py`,
  `tests/test_iam_graphql.py`: repoint imports to `angee.integrate.*` /
  `angee.iam_integrate_oidc.*`; split connection vs login test models.

## Migration approach

No data to preserve → **regenerate**. `angee build`, drop & recreate
`runtime/*/migrations` for `iam`, `integrate`, `iam_integrate_oidc` (preserve the
`*/migrations/` rule per AGENTS.md — regenerate, don't hand-delete others), fresh
`migrate`, `rebac sync`, `resources load`. No `SeparateDatabaseAndState`, no
dual-write, no `is_oidc` compatibility shim.

## Sequence

1. Scaffold `integrate/oauth/` + move `oidc/`, `credentials.py`; split
   `OAuthClient` → `OAuthClient` + `OidcClient`; move `ExternalAccount`,
   `Credential` into `integrate/models.py`. Drop `is_oidc`.
2. Move credential/connection GraphQL types + connect/unlink flows into
   `integrate/schema.py`; remove the up-imports.
3. Move rebac defs (rename to `integrate/*`), settings, seed, `oauth_clients`
   command into integrate.
4. Create `iam_integrate_oidc` with resolver + login/link mutations +
   `OidcClient` login-policy extension + `available_connections`.
5. Strip `iam` down to identity; delete dead OIDC/credential code.
6. Repoint `agents`, example seeds, tests.
7. Regenerate runtime + migrations; full verify.

## Verification (DoD)

From repo root:
```
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py makemigrations iam integrate iam_integrate_oidc notes
uv run examples/notes-angee/manage.py migrate
uv run examples/notes-angee/manage.py rebac sync
uv run examples/notes-angee/manage.py resources load
uv run examples/notes-angee/manage.py schema --check
uv run pytest tests
```
Plus architecture/django review on the diff.

## As-built (status: DONE — verified)

Verified green: `angee build`, `makemigrations` (fresh), `migrate`, `rebac sync`,
`resources load` (master/install/demo), `schema --check`, `ruff`, `mypy`
(40 files), `pytest` (377 passed). The dev DB was reset (no compat migrations).

**The clean three-addon split — OAuth → OIDC inheritance across the boundary:**

- **`integrate` is pure OAuth.** It owns `OAuthClient` (connect-for-API base, incl.
  `userinfo_endpoint` + claim mapping so connect can label an account), the OAuth
  protocol (`integrate/oauth/`: `OAuthClientProtocol`, state, errors, browser-flow),
  `ExternalAccount`, `Credential`, the connect/disconnect flow, and the OAuth admin
  CRUD. It has **no OIDC of any kind** and never references the login addon.
- **`iam_integrate_oidc` is OIDC, extending integrate's OAuth and composing iam.**
  It owns the `OidcClient` model (1:1 refinement of `integrate.OAuthClient`:
  issuer/JWKS/discovery + login policy), the OIDC protocol
  (`protocol.OidcClientProtocol(OAuthClientProtocol)` — real class inheritance over
  integrate's base), ID-token verification, the login/link flow + resolver + session
  bind, the OIDC admin CRUD/discover, and the last-sign-in delete guard. It
  `depends_on (iam, integrate)`.
- So the OAuth→OIDC inheritance is literal and spans the addon boundary: the data
  refinement (`OidcClient.oauth_client` 1:1) and the protocol subclass both live in
  the OIDC addon and extend integrate's OAuth.

Other decisions, with rationale:

- **`userinfo_endpoint` + claim mapping are on the OAuth base, not OIDC.** The
  install seed proves connect-only providers (Anthropic) read userinfo + claims to
  label the account. Only id-token trust (`issuer`/`jwks_uri`/`discovery_url` +
  `verify_id_token`) is OIDC.
- **`unlink_account` → `integrate.disconnect_account`** (generic, login-agnostic);
  the last-sign-in guard is a `pre_delete` veto wired by `iam_integrate_oidc` via
  `apps.lazy_model_operation` (raises `OAuthFlowError("only_sign_in_method")`,
  surfaced as a typed result).
- **GraphQL/REBAC/settings renames:** start payload `OidcStartPayload` →
  `OAuthStartPayload`; `OidcFlowError` → `OAuthFlowError`; REBAC
  `auth/{oauth_client,external_account,credential}` → `integrate/{...}` and
  `iam_integrate_oidc/oidc_client`; settings `ANGEE_IAM_*` → `ANGEE_INTEGRATE_*`
  (OAuth) and `ANGEE_OIDC_DISCOVERY_TTL` (login addon). `OAuthClientType` lost its
  nested `oidc` projection (a cross-addon GraphQL nest); the login addon exposes
  `OidcClientType` + `oidcClients` separately.
- **Seeds:** integrate ships `010_integrate.oauthclient` (OAuth base, xrefs
  `oauth_*`); the login addon ships `010_iam_integrate_oidc.oidcclient` (OIDC
  refinements), referencing integrate's clients by **cross-addon xref**
  (`integrate.oauth_gemini`) — verified resolvable by the resource loader.
  Idempotency is by xref, not `adopt` (a FK adopt key filtered the raw xref against
  the `id` column).
- **`sync_from_settings` is OAuth-only.** Settings-driven OIDC config was dropped
  (OIDC providers are configured via the login addon's seed/console). If a host ever
  needs settings-driven OIDC, add a sync to the login addon.
- Name kept as requested: `angee.iam_integrate_oidc`.

## Follow-up (out of scope of this change)

- **Frontend codegen** (`addons/angee/iam/web`): regenerate `documents.ts` against
  the new SDL — the only break is the `OidcStartPayload` interface name →
  `OAuthStartPayload`. No hand-written query changes (`unlinkAccount` is unused;
  all operation/field names — `loginStart`, `connectAccountComplete`,
  `availableConnections`, `isOidc` — are preserved).
