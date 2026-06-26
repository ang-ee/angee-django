// The browser-facing callback paths for the outbound account-connect flow. They live
// under integrate so OAuth client registrations and provider connect flows share them.

// The canonical mounted callback route, proposed to the backend at connect-start.
export const CONNECT_CALLBACK_PATH = "/integrate/oauth/callback";
// The bare loopback callback a fixed public client's allow-list requires on localhost
// (e.g. Anthropic, whose public client allow-lists only `/callback`). The backend
// (OAuthClient.loopback_redirect_path) rewrites the localhost redirect to this path; we
// mount a matching route below so the provider can redirect back to it. Keep in sync
// with the seeded `loopback_redirect_path` (pinned by both tests/test_connections.py and
// index.test.ts so drift fails a test, not a connect).
export const CONNECT_CALLBACK_LOOPBACK_PATH = "/callback";

/** The absolute canonical callback URL proposed when starting a connect flow. */
export function connectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return `${window.location.origin}${CONNECT_CALLBACK_PATH}`;
}

/** The callback URL of the route currently handling the redirect. The page is mounted at
 *  the exact `redirect_uri` the provider returned to, so it is simply origin + pathname —
 *  this matches whichever route (canonical or loopback) the backend's redirect resolved to. */
export function currentConnectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return `${window.location.origin}${window.location.pathname}`;
}
