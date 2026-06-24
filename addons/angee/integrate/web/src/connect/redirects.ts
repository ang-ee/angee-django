// The browser-facing callback path for the outbound account-connect flow. It lives
// under integrate so OAuth client registrations and provider connect flows share
// one canonical URL.

export const CONNECT_CALLBACK_PATH = "/integrate/oauth/callback";
export const CONNECT_CALLBACK_FALLBACK_PATH = "/callback";

/** The absolute callback URL used when connecting an external account. */
export function connectCallbackRedirectUri(
  path = CONNECT_CALLBACK_PATH,
): string {
  if (typeof window === "undefined") return path;
  return `${window.location.origin}${path}`;
}

/** The callback URL that matches the currently mounted callback route. */
export function currentConnectCallbackRedirectUri(): string {
  if (typeof window === "undefined") return CONNECT_CALLBACK_PATH;
  return connectCallbackRedirectUri(
    window.location.pathname === CONNECT_CALLBACK_FALLBACK_PATH
      ? CONNECT_CALLBACK_FALLBACK_PATH
      : CONNECT_CALLBACK_PATH,
  );
}

/** Return the callback path a connectable record needs, if it is not canonical. */
export function connectCallbackPathForRecord(
  record: Record<string, unknown>,
): string | undefined {
  return [
    record.backend_class,
    record.impl_class,
    nestedValue(record.vendor, "slug"),
    nestedValue(record.vendor, "display_name"),
  ].some(isAnthropicConnectRecord)
    ? CONNECT_CALLBACK_FALLBACK_PATH
    : undefined;
}

function isAnthropicConnectRecord(value: unknown): boolean {
  return String(value ?? "").trim().toLowerCase() === "anthropic";
}

function nestedValue(value: unknown, key: string): unknown {
  if (value === null || typeof value !== "object") return undefined;
  return (value as Record<string, unknown>)[key];
}
