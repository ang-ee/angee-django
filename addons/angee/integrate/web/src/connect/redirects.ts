// The browser-facing callback paths for the outbound account-connect flow. The
// path strings are stable (provider redirect-uri registrations point at them); only
// the route names moved from `iam.connect.*` to `integrate.connect.*`.

export const CONNECT_CALLBACK_PATH = "/callback";
export const LEGACY_CONNECT_CALLBACK_PATH = "/iam/oauth/callback";

/** The absolute callback URL used when connecting an external account. */
export function connectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return `${window.location.origin}${CONNECT_CALLBACK_PATH}`;
}
