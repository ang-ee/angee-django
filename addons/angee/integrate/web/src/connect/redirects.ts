// The browser-facing callback path for the outbound account-connect flow. It lives
// under integrate so OAuth client registrations and provider connect flows share
// one canonical URL.

export const CONNECT_CALLBACK_PATH = "/integrate/oauth/callback";

/** The absolute callback URL used when connecting an external account. */
export function connectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return `${window.location.origin}${CONNECT_CALLBACK_PATH}`;
}

/** The callback URL used by the canonical mounted callback route. */
export function currentConnectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return connectCallbackRedirectUri();
}
