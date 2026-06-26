type FetchFn = typeof globalThis.fetch;

/** Derive the GraphQL-over-WebSocket URL from an http(s) endpoint. */
export function graphQLWebSocketUrl(endpoint: string, origin?: string): string {
  const base =
    origin ?? (typeof location !== "undefined" ? location.origin : undefined);
  const url = new URL(endpoint, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** A deduplicating, cacheable source of the Django CSRF token. */
export interface CsrfTokenProvider {
  token(): Promise<string | null>;
  clear(): void;
}

export interface CsrfTokenOptions {
  endpoint?: string;
  fetch?: FetchFn;
}

export function createCsrfTokenProvider(
  options: CsrfTokenOptions = {},
): CsrfTokenProvider {
  const endpoint = options.endpoint ?? "/auth/csrf/";
  const fetchImpl = options.fetch ?? globalThis.fetch;
  let cached: string | null = null;
  let inFlight: Promise<string | null> | null = null;

  async function load(): Promise<string | null> {
    const response = await fetchImpl(endpoint, { credentials: "include" });
    if (!response.ok) return null;
    const body = (await response.json()) as { token?: unknown };
    return typeof body.token === "string" ? body.token : null;
  }

  return {
    async token() {
      if (cached !== null) return cached;
      inFlight ??= load().then((token) => {
        cached = token;
        inFlight = null;
        return token;
      });
      return inFlight;
    },
    clear() {
      cached = null;
      inFlight = null;
    },
  };
}

export type AuthFetch = (baseFetch: FetchFn) => FetchFn;

export function sessionAuth(options: CsrfTokenOptions = {}): AuthFetch {
  return (baseFetch) => {
    const csrf = createCsrfTokenProvider({
      endpoint: options.endpoint,
      fetch: options.fetch ?? baseFetch,
    });
    return async (input, init) => {
      const headers = new Headers(init?.headers);
      if (!headers.has("x-csrftoken")) {
        const token = await csrf.token();
        if (token) headers.set("x-csrftoken", token);
      }
      return baseFetch(input, { ...init, credentials: "include", headers });
    };
  };
}

export function bearerAuth(token: string): AuthFetch {
  return (baseFetch) => (input, init) => {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return baseFetch(input, { ...init, headers });
  };
}

/**
 * Like {@link bearerAuth}, but reads the token per request from `getToken` so a
 * rotated token flows through without rebuilding the client. When the getter
 * returns null the `Authorization` header is omitted.
 */
export function bearerAuthFromGetter(getToken: () => string | null): AuthFetch {
  return (baseFetch) => (input, init) => {
    const headers = new Headers(init?.headers);
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return baseFetch(input, { ...init, headers });
  };
}

const FATAL_WS_CLOSE_CODES = new Set([1000, 1008, 4400, 4401, 4403, 4406, 4409]);

/** Whether a graphql-ws close code is terminal rather than retryable. */
export function isFatalGraphQLWsCloseCode(code: number): boolean {
  return FATAL_WS_CLOSE_CODES.has(code);
}

/** Whether a graphql-ws close event is terminal rather than retryable. */
export function isFatalGraphQLWsClose(event: unknown): boolean {
  return (
    typeof CloseEvent !== "undefined" &&
    event instanceof CloseEvent &&
    isFatalGraphQLWsCloseCode(event.code)
  );
}
