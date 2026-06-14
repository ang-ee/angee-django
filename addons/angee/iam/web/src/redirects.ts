import { safeRedirectPath } from "@angee/base";

export const LOGIN_CALLBACK_PATH = "/sso/callback";
export const LEGACY_LOGIN_CALLBACK_PATH = "/login/callback";
export const CONNECT_CALLBACK_PATH = "/callback";
export const LEGACY_CONNECT_CALLBACK_PATH = "/iam/oauth/callback";
export const DEFAULT_NEXT_PATH = "/";

/** The absolute callback URL the OAuth provider redirects back to. */
export function loginCallbackRedirectUri(): string {
  if (typeof window === "undefined") return LOGIN_CALLBACK_PATH;
  return `${window.location.origin}${LOGIN_CALLBACK_PATH}`;
}

/** The absolute callback URL used when connecting an external account. */
export function connectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return `${window.location.origin}${CONNECT_CALLBACK_PATH}`;
}

/** The validated post-login `next` path carried on the current location. */
export function loginNextFromLocation(): string {
  if (typeof window === "undefined") return DEFAULT_NEXT_PATH;
  const params = new URLSearchParams(window.location.search);
  return safeRedirectPath(params.get("next")) ?? DEFAULT_NEXT_PATH;
}
