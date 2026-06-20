# Plan: split the federation/OIDC frontend to match the backend `iam`/`integrate`/`iam_integrate_oidc` split

Status: DONE — verified (typecheck + vitest + host Vite build all green;
base 192 / iam 13 / integrate 15 / host 6 tests pass). The model-driven
`OidcProvidersPage` (iam) and `ProvidersPage` (integrate) still want a runtime
smoke-test in the running console (DataPages carry no unit tests).
Companion to `.agents/plans/iam-integrate-oidc-split.md` (the backend split, DONE).
Scope: **frontend only.** The backend three-addon split stays as verified; this
realigns the React packages to it.

2026-06 clean-MTI update: outbound connect now uses the integrate-owned
canonical callback path `/integrate/oauth/callback`; `/iam/oauth/callback` was
intentionally removed as part of the pre-1.0 break. The mounted `/callback`
route is no longer a generic legacy alias — it remains only as an integrate
connect fallback for providers such as Anthropic that reject nested callback
paths.

## Decision (by the architect, over several iterations)

The frontend splits **by direction**, not 1:1 with the backend addons:

- **Inbound authentication → `@angee/iam`** ("what logs users in"): the OIDC login
  surface and the OIDC-provider admin.
- **Outbound connect-for-API → `@angee/integrate`** ("the rest — outbound
  credentials and so on"): OAuth providers, external accounts, credentials, the
  connect flow.
- **No "Federation" menu** verbatim. Each block sits in its own cohesive subfolder
  so it is trivially relocatable later (e.g. into a future `iam_integrate_oidc/web`).

The backend op buckets already merge into the shared `public`/`console` SDL, so
either package can issue any op regardless of which backend addon contributed it.

## Why this shape

The backend split left `iam` with **zero** substrate references (inbound auth never
reads the OAuth models). The frontend mirrors the same boundary by *direction*:
sign-in (inbound) is identity → `iam`; connecting outbound API accounts is
integration → `integrate`. The model split forces the old single `ProvidersPage`
(fused OAuth+OIDC behind `is_oidc`) to become **two** model-driven pages, because
`OAuthClientType` (integrate) no longer carries `issuer`/`jwksUri`/`isOidc`/login
policy — those live on `OidcClientType` (a 1:1 refinement).

## Target topology

```
@angee/base   ── owns the shared OAuthCallback shell (auth/) + login slot seams
   ▲
@angee/iam        inbound auth: OIDC login methods slot, login callback,
   │              OIDC Providers page (OidcClient + discover)
@angee/integrate  outbound connect: OAuth Providers page (OAuthClient + connect),
                  External Accounts, Credentials (+ form), connect callback
```

## Move inventory

**→ `@angee/base` (`src/auth/`)** — the generic redirect-callback shell, shared by
both callbacks (no cross-addon web-import precedent; duplication is the DRY smell):
- `OAuthCallback.tsx` (+ test) moved from `iam/web`; exported via `auth/index.ts`.

**`@angee/iam` keeps / gains (inbound auth):**
- keeps `OAuthLoginMethods` slot and the canonical login callback route (`/sso/callback`),
  `redirects.ts` login half, login `documents` (`availableConnections`,
  `loginStart`, `loginComplete`), `discoverOidcEndpoints`.
- `OAuthCallbackPage` repointed to import `OAuthCallback` from `@angee/base`.
- **new** `views/OidcProvidersPage.tsx` — model-driven over `OidcClient`
  (oauthClient ref, issuer/discovery/jwks, link/create policy, allowed domains) +
  the `discoverOidcEndpoints` action (addressed by the OidcClient id).
- loses the "Federation" menu group; gains an "OIDC Providers" item.

**`@angee/integrate` gains (outbound connect, in `src/connect/`):**
- `OAuthConnectCallbackPage.tsx`, connect `redirects.ts`, connect `documents.ts`
  (`connectAccountStart/Complete`, `revealCredential`).
- `views/ProvidersPage.tsx` over `OAuthClient` (OAuth-only fields) + connect action.
- `views/ExternalAccountsPage.tsx`, `views/CredentialsPage.tsx`, `credential-form.tsx`.
- i18n keys under the `integrate` namespace; manifest routes + a "Connections" menu
  group + the `Credential` form.

## Notes / gotchas

- `OAuthStartPayload` is the SDL name (the `OidcStartPayload` TS interface was
  stale); each package keeps its own copy of the subset it selects.
- OAuth connect-readiness drops the OIDC `discoveryUrl` branch — the OAuth base has
  no discovery; readiness = `isEnabled && clientId && authorizeEndpoint && tokenEndpoint`.
- `OidcClient.oauthClient` is a relation on read but `ID!` on write → `many2one`,
  `createOnly` (absent from `OidcClientPatch`).
- Callback path strings were originally unchanged, but the clean-MTI pre-1.0
  pass superseded that compatibility note for outbound connect:
  `/integrate/oauth/callback` is now canonical, `/iam/oauth/callback` is removed,
  and `/callback` is a provider-specific integrate connect fallback. Inbound
  sign-in callbacks remain owned by `iam`.

## Verify (DoD)

`pnpm run typecheck && pnpm run test && pnpm run build` for `@angee/base`,
`@angee/iam`, `@angee/integrate`, plus the host
`addon-composition.test.tsx` (icon/menu composition). React + architecture review on
the diff.
