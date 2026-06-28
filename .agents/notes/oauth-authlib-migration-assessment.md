# OAuth/OIDC → Authlib migration assessment

> Workspace: `workspace/integrate-authlib` (from `main`). Produced by the
> ultracode map-and-assess workflow (5 read-only mappers + architect synthesis).
> This is the design of record for the migration; implementation follows it.

## Headline correction

The earlier "build-vs-buy" audit reported ~5.1K LOC of hand-rolled OAuth. That
figure was the **whole `integrate` addon** (models.py alone is ~2,059 LOC of
domain: VCS bridge, webhooks, repositories, templates, credentials). The actual
hand-rolled **OAuth/OIDC protocol layer** is **893 LOC**, of which only
**~310 LOC is genuine protocol mechanism** that Authlib owns and we can delete.
The rest is Angee domain logic with no library equivalent and stays.

---

## Authlib migration assessment — `integrate` OAuth/OIDC substrate

### 1. Verdict

The hand-rolled protocol layer (the `addons/angee/integrate/oauth/` package plus
`iam_integrate_oidc/protocol.py`) is **893 LOC**. Of that, roughly **310 LOC is
genuine OAuth2/OIDC *protocol mechanism*** — authorization-URL building, the
token-endpoint POST dispatch, the urllib HTTP transport, PKCE S256, and the
PyJWT/PyJWKClient id_token verification. That mechanism is exactly what Authlib
owns, so it can be deleted and replaced by a **thin per-`OAuthClient` adapter**
(~140 LOC) behind the *same* public signatures. Net: ~170 fewer LOC, and the
deleted code is the security-sensitive part now owned by a maintained library.

The remaining ~580 LOC of the "protocol layer" is domain-flavored and **stays**:
the Angee error vocabulary (`errors.py`), the single-use Django-cache state store
and `StateFlow`/`StateRecord` (`state.py`), session-binding and redirect-safety
(`flow.py`), claim mapping and endpoint projection (`models.py`), and credential
storage/refresh orchestration (`credentials.py`). Authlib has no equivalent for
any of these — they are owners with no library substitute, per Angee's rules.

The seam stays byte-for-byte stable. `iam_integrate_oidc` and every `schema.py`
caller keep calling `OAuthClientProtocol(...).authorize_url(...)`,
`.exchange_code(...)`, `.refresh_token(...)`, and
`OAuthClientOidcProtocol(...).verify_id_token(...)` with identical signatures.
**No caller changes.**

### 2. What deletes vs what stays

| File / symbol | LOC today | LOC after | Δ | Class |
|---|---:|---:|---:|---|
| `oauth/client.py` — transport helpers (`_get_json`, `_post_form`, `_post_json`, `_post_form_no_response`, `_loads_json`, `_http_error_body`) | ~93 | 0 | −93 | protocol mechanism (urllib) → Authlib `OAuth2Session` |
| `oauth/client.py` — `_token_request` | 45 | 0 | −45 | protocol mechanism → `OAuth2Session.fetch_token` |
| `oauth/client.py` — `_authorize_query`, `_with_query`, `_param_values` | ~36 | ~8 | −28 | URL/param building → `create_authorization_url` (params kept as Authlib `client_kwargs`) |
| `oauth/client.py` — `authorize_url` / `exchange_code` / `refresh_token` / `fetch_userinfo` / `revoke_token` bodies | ~83 | ~40 | −43 | gut to thin delegators (signatures unchanged) |
| `oauth/client.py` — `ensure_endpoints`, `_endpoint`, `_safe_error_body` (redaction), docstrings, adapter factory | ~26 | ~85 | +59 | kept + new `OAuth2Session` builder glue |
| **`oauth/client.py` subtotal** | **373** | **~140** | **−233** | |
| `iam_integrate_oidc/protocol.py` — `verify_id_token` + `_audience_matches` | ~54 | ~22 | −32 | PyJWT/PyJWKClient → `authlib.jose` JWT + JWKS (keep nonce/iss/aud + `OAuthFlowError` mapping) |
| `oauth/discovery.py` — `_get_json` fetch + parse | ~12 | ~6 | −6 | parse/validate → `authlib.oidc.discovery.OpenIDProviderMetadata`; keep Django-cache wrapper |
| `oauth/flow.py` — `pkce_challenge` | 8 | 8 | 0 | trivial S256; **recommend keep** to avoid caller churn (Authlib could own it) |
| **Protocol mechanism total** | **~310 deletable** | | **−≈310 / +≈140 ⇒ net −≈170** | |
| `oauth/state.py` (StateFlow, StateRecord, single-use cache issue/consume) | 100 | 100 | 0 | **domain — keep** (Authlib has no Django-cache single-use store) |
| `oauth/flow.py` (session binding, `consume_validated_state`, `coerce_next_path`, `enabled_oauth_client`) | ~116 | ~116 | 0 | **domain — keep** |
| `oauth/errors.py` (codes + `OAuthFlowError` + sanitization) | 80 | 80 | 0 | **domain — keep** (public error contract) |
| `models.py` OAuthClient/ExternalAccount/Credential, claim mapping, `resolve_connect_redirect`, `discover_endpoints` projection | (domain) | (domain) | 0 | **domain — keep** |
| `credentials.py` handler registry + `OAuthCredentialHandler` upsert/refresh orchestration | (domain) | (domain) | 0 | **domain — keep** (refresh delegates to the seam) |
| `connect.py` / `iam_integrate_oidc/identity.py` / `models.py` / `signals.py` | (domain) | (domain) | 0 | **domain — keep** |
| `net.py` SSRF + `http.py` pinned transport | (security) | (security) | 0 | **keep** |

> Two mappers (oidc-discovery, callers-seam) marked `state.py issue/consume` and
> all of `discovery.py` deletable. **That is carried forward as a disagreement
> and rejected**: Authlib has no Django-cache, no single-use semantics, and no
> `StateFlow` cross-flow replay guard — those are domain owners. Only the
> *parsing/validation* inside discovery and the *transport* are Authlib's.

### 3. The seam (unchanged signatures, Authlib-backed bodies)

The protocol class becomes a thin adapter that builds one Authlib
`OAuth2Session` per `OAuthClient` row and delegates. Public signatures are
preserved exactly:

```python
class OAuthClientProtocol:
    def __init__(self, oauth_client): ...
    def authorize_url(self, *, state, redirect_uri, scopes, code_challenge=None) -> str
    def exchange_code(self, *, code, redirect_uri, code_verifier=None, state=None) -> dict
    def refresh_token(self, *, refresh_token) -> dict
    def fetch_userinfo(self, access_token) -> dict
    def revoke_token(self, token) -> None
    def ensure_endpoints(self) -> dict
```

Backing:

| Seam method | Authlib primitive |
|---|---|
| `authorize_url` | `authlib.integrations.requests_client.OAuth2Session.create_authorization_url(authorize_endpoint, state=…, code_challenge=…)` |
| `exchange_code` | `OAuth2Session.fetch_token(token_endpoint, code=…, code_verifier=…, grant_type="authorization_code")` |
| `refresh_token` | `OAuth2Session.refresh_token(token_endpoint, refresh_token=…)` (Authlib auto-refresh **disabled**) |
| `fetch_userinfo` | `OAuth2Session.get(userinfo_endpoint)` (bearer auto-injected); keep best-effort `{}` on failure |
| `revoke_token` | `OAuth2Session.revoke_token(revoke_endpoint, token, token_type_hint="access_token")` (RFC 7009 — *medium confidence*, see risks) |
| `ensure_endpoints` / `_endpoint` | unchanged — delegates to `oauth_client.discover_endpoints()` (domain) |

OIDC extension, signatures preserved:

```python
class OAuthClientOidcProtocol(OAuthClientProtocol):
    def authorize_url(self, *, state, redirect_uri, scopes, nonce=None, code_challenge=None) -> str
    def verify_id_token(self, id_token, *, nonce=None, _jwks_client=None) -> dict
```

`verify_id_token` swaps PyJWT/PyJWKClient for `authlib.jose` (`JsonWebToken` /
`jwt.decode`) with a JWKS key set, keeping the explicit `iss`/`aud`(string|array)
/`nonce`/`exp` checks and the `OAuthFlowError(INVALID_ID_TOKEN, 400)` mapping.
The `_jwks_client` test seam is retained (now an injected JWKS key set).

Discovery, state, flow, connect, identity public functions
(`discovery_document`, `state.issue`/`consume`, `flow.issue_flow`/
`consume_validated_state`/`pkce_challenge`, `complete_account_connect`,
`complete_login`/`complete_link`/`resolve`) are unchanged.

### 4. Dependencies & stack

- `uv add authlib` → `authlib>=1.6`. Authlib's JOSE needs `cryptography`
  (already locked). The **`requests_client` integration pulls in `requests`** as
  a new transitive dependency (the codebase is sync urllib today, no
  requests/httpx). Confirm that is acceptable, or use `httpx_client` if httpx is
  already present, or use Authlib's protocol core over Angee's own pinned transport.
- Add a `docs/stack.md` Backend row for `authlib` and amend the existing
  `pyjwt[crypto]` row (id_token verification moves to `authlib.jose`).

### 5. Authlib boundaries — what it does NOT cleanly own (thin Angee shim stays)

- **Per-provider token body format**: Angee supports form *or* JSON token bodies
  (`token_request_format_value`) plus arbitrary `authorize_param_values` /
  `token_param_values` (Anthropic and other presets). Authlib defaults to
  form-encoded; JSON bodies and custom params need explicit per-row config. Shim
  stays in the adapter factory.
- **SSRF / transport pinning**: Authlib over `requests` bypasses Angee's
  DNS-pinned `http.py`. The OAuth path is unaffected for parity (it already uses
  *unpinned* urllib against operator-trusted endpoints), but `discovery_url` /
  `jwks_uri` fetches ideally stay pinned.
- **State / nonce single-use store, cross-flow replay guard, session binding,
  REBAC scoping, credential encryption/refresh-lock, claim mapping, error
  vocabulary**: all Angee domain owners — kept.

### 6. Migration plan (ordered)

1. `uv add authlib`; add the `authlib` stack.md row and amend the `pyjwt` row;
   `uv lock`.
2. Build the adapter factory `_session(oauth_client) -> OAuth2Session` mapping
   row fields (`client_id`, `client_secret`, scope, `token_request_format_value`,
   `authorize_param_values`/`token_param_values`, `supports_pkce`, endpoints) to
   Authlib config, with auto-refresh disabled.
3. Reimplement the 6 `OAuthClientProtocol` methods as thin delegators over the
   session; keep signatures, `ensure_endpoints`/`_endpoint`, and
   `_safe_error_body` redaction.
4. Replace `verify_id_token` with `authlib.jose` JWT + JWKS; keep nonce/iss/aud
   checks, `OAuthFlowError` mapping, and the `_jwks_client` test seam.
5. Point discovery parsing at `authlib.oidc.discovery.OpenIDProviderMetadata`;
   keep the Django-cache wrapper and TTL.
6. Decide `pkce_challenge`: keep as-is (recommended — no caller churn) or let the
   adapter own it; either way callers are untouched.
7. Run unit tests for connect / login / link / refresh / revoke; add
   provider-quirk fixtures (form vs JSON token body, `aud` array, refresh
   rotation, kid-miss JWKS refetch).
8. `uv run examples/notes-angee/manage.py angee build`, `makemigrations`
   (none expected), `schema --check`; run the OIDC login e2e.
9. Delete the dead transport + `_token_request` + query helpers; confirm no
   `urllib` import remains in `oauth/`.
10. Remove `pyjwt[crypto]` **only after** confirming no other JWT consumer (e.g.
    MCP bearer); otherwise keep it.

### 7. Risks

- **Token body / param quirks** (mapper confidence *medium*): JSON-body token
  endpoints and custom authorize/token params must be reproduced via Authlib
  config; verify each preset provider.
- **Refresh rotation race**: `Credential._refresh_locked` (SELECT FOR UPDATE)
  stays the serialization owner. Authlib auto-refresh/auto-persist must be
  **off** so it cannot replay a rotated refresh token behind the lock.
- **SSRF**: Authlib's requests session bypasses the pinned transport; OAuth
  endpoints are operator-trusted so parity holds, but decide whether
  discovery/JWKS fetches should route through `http.py`.
- **JWKS rotation**: PyJWKClient caches signing keys and refetches on kid-miss;
  ensure `authlib.jose` JWKS handling reproduces kid-miss refetch.
- **PKCE / nonce**: keep S256 (never plain); keep explicit nonce binding +
  id_token nonce check.
- **Userinfo best-effort**: connect tolerates userinfo failure (`return {}`);
  the adapter must swallow, not raise.
- **RFC 7009 revoke** (confidence *medium*): confirm `OAuth2Session.revoke_token`
  covers `token_type_hint` + client auth; else keep a thin form POST.
- **Error redaction**: route Authlib error bodies through `_safe_error_body` so
  secrets stay out of logs.

### 8. Open questions

- Sync vs async + transport: accept `requests` as a new transitive dep via
  `requests_client`, or use `httpx_client`, or Authlib-core-over-pinned-transport?
- Does anything else consume `pyjwt[crypto]` (MCP bearer)? If not, consolidate
  all JWT on `authlib.jose` and drop pyjwt; if yes, keep both.
- Should discovery/JWKS fetches move onto the SSRF-pinned `http.py` as part of
  this change (pre-existing gap)?
- Enumerate every `ImplClassField` OAuth provider preset and confirm Authlib
  config reproduces its `authorize_param_values` / `token_param_values` /
  token format.
- Keep `pkce_challenge` as a public helper (seam stability) or delete it?
- Does Authlib's metadata-model validation reject any provider's non-conformant
  discovery document that today's lenient dict-fetch tolerates?

---

## Revisions during implementation (what actually shipped)

The implementation deviates from the original plan in four deliberate ways,
discovered by grounding the design in the real runtime:

1. **PyJWT kept; `authlib.jose` NOT adopted.** Importing `authlib.jose` warns it
   is deprecated ("use joserfc instead"). `verify_id_token` was already a clean
   delegation to PyJWT + PyJWKClient (JWKS fetch + kid-miss refetch), never the
   reinvention. So `iam_integrate_oidc/protocol.py` is untouched and pyjwt stays.
   Migrating it would have traded working code for a deprecated module.

2. **Authorize-URL builders kept** (`_authorize_query` / `_with_query`). They are
   deterministic string-building, not the security-sensitive surface, and the seam
   passes a pre-hashed `code_challenge` while Authlib's `create_authorization_url`
   wants the raw verifier — keeping them preserves the seam with zero caller churn
   (incl. the OIDC subclass).

3. **`state` dropped from the token request (both paths).** Authlib does not post
   `state` to the token endpoint (it is an authorize-redirect CSRF param, RFC 6749
   §4.1.3, validated separately by `consume_validated_state`). The old code posted
   it; the new code omits it in both the Authlib form path and the JSON shim. One
   test assertion (`test_exchange_code_posts_json_body_...`) was updated to the
   spec-correct body — a fix, not a weakening.

4. **JSON-token-body shim retained.** No real provider uses `token_request_format
   == "json"` (only a test), but it is a supported config option; Authlib form-
   encodes per RFC, so the JSON path is a thin documented httpx POST through the
   same `_transport` seam. `revoke_token` now uses Authlib (`client_secret_basic`
   client auth) instead of posting creds in the body — best-effort, spec-valid.

**Net shape.** `oauth/client.py` went from urllib transport + grant dispatch to a
~140-LOC Authlib `OAuth2Client` adapter (delete the 6 urllib helpers + `_token_request`);
no model change, no migration. Test seam moved from monkeypatching private
`_post_form`/`_post_json` to injecting `httpx.MockTransport` via `self._transport`
(a stronger seam — it exercises Authlib's real token parsing).

**Verification (all from the workspace root):** ruff check + format clean; mypy
clean on the two touched files; `uv run pytest` → **661 passed** (the lone
`tests/test_asgi.py` collection error reproduces identically on pristine `main`
— pre-existing, unrelated); `angee build` ok; `schema --check` ok. New deps:
`authlib>=1.7.2`, `httpx>=0.28.1`; pyjwt retained.

## Verify phase — adversarial review + fixes

A three-lens adversarial review (architecture, Django-runtime, security) ran on
the diff. All three independently reproduced **one real high-severity
regression**, plus smaller findings. Fixes applied:

- **[high] Non-JSON token error leaked `JSONDecodeError`.** Authlib parses the
  token body as JSON *before* checking the status, so a non-JSON 4xx (the
  documented Anthropic/CDN 403/429 block page) raised a bare `ValueError` that
  escaped `_authlib_token_request`'s `except (OAuthError, httpx.HTTPError)`,
  becoming an unhandled 500 instead of `OAuthFlowError`. The old urllib code and
  the sibling `_json_token_request`/`_get_json` caught it. Fixed: widened to
  catch `httpx.HTTPStatusError` (real status) and `(httpx.HTTPError, ValueError)`,
  each routed through `_log_token_failure` + `OAuthFlowError`. Regression test
  added (`test_exchange_code_form_path_maps_non_json_error_to_oauth_flow_error`).
- **[medium] Token-failure logging only fired on the `OAuthError` branch** —
  transport/HTTP failures went silent, defeating the Anthropic-edge diagnostics.
  Now every failure branch logs (redacted) with the provider's real status.
- **[medium] TLS trust silently moved to certifi.** httpx defaults `verify=True`
  to the certifi bundle, but `docs/backend/guidelines.md` Pitfalls mandates the
  stdlib system trust store (`ssl.create_default_context()`), which `http.py` and
  the same-flow `PyJWKClient` use. Fixed via a shared `_outbound_kwargs()` that
  sets `verify=ssl.create_default_context()` for real connections.
- **[low] Dead `code_challenge_method="S256"` session config** — Authlib reads it
  only in `create_authorization_url`, which this adapter never calls (PKCE rides
  the `code_verifier` in the grant). Removed.
- **[low] Outbound httpx policy (UA/timeout/transport/verify) was duplicated three
  ways.** Centralized in `_outbound_kwargs()`.
- **Coverage:** added form-path refresh and public-client (no-secret, asserts
  `client_id` + `code_verifier`, no `client_secret`) tests — the gaps that let the
  high finding ship unnoticed.

**Deliberately not fixed (noted for follow-up):** the pre-existing
`getattr(self.oauth_client, …, default)` probing (predates this change; wants a
typed contract on `OAuthClient`, a broader refactor) and the low-probability case
where a `token_param_values` key collides with an httpx client-kwarg name and is
dropped on the Authlib path.

**Behavior change (intentional, RFC-correct):** `state` is no longer posted to the
token endpoint (RFC 6749 §4.1.3; CSRF is enforced by `flow.consume_validated_state`).

**Re-verification after fixes:** ruff + mypy clean; targeted suite **57 passed**;
full suite **664 passed** (+3 new tests; same pre-existing `test_asgi.py`
collection error excluded); `angee build` + `schema --check` ok.

**Note on PEP 758:** the codebase targets Python 3.14 and uses unparenthesized
`except A, B:` (valid since PEP 758, catches both) — house style, not a bug.
