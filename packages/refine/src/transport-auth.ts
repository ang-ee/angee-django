type FetchFn = typeof globalThis.fetch;

/** Derive the GraphQL-over-WebSocket URL from an http(s) endpoint. */
export function graphQLWebSocketUrl(endpoint: string, origin?: string): string {
  const base =
    origin ?? (typeof location !== "undefined" ? location.origin : undefined);
  const url = new URL(endpoint, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** A deduplicating source of Django's current CSRF token, including login rotation. */
export interface CsrfTokenProvider {
  token(): Promise<string | null>;
  clear(): void;
}

export interface CsrfTokenOptions {
  endpoint?: string;
  fetch?: FetchFn;
}

interface CsrfBootstrap {
  token: string | null;
  cookieName: string | null;
}

// The CSRF cookie belongs to the document, not to one GraphQL client. Separate
// concurrent bootstraps would each mint a secret, the last Set-Cookie would win,
// and the other clients' tokens would no longer match it. Every provider shares
// one in-flight bootstrap per endpoint.
const bootstraps = new Map<string, Promise<CsrfBootstrap>>();

/** Django accepts the unmasked CSRF cookie; read it when the cookie is readable. */
function readCsrfCookie(cookieName: string | null): string | null {
  if (!cookieName || typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(cookieName)}=`;
  const cookie = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
}

export function createCsrfTokenProvider(
  options: CsrfTokenOptions = {},
): CsrfTokenProvider {
  const endpoint = options.endpoint ?? "/auth/csrf/";
  const fetchImpl = options.fetch ?? globalThis.fetch;
  let cookieName: string | null = null;

  async function load(): Promise<CsrfBootstrap> {
    const response = await fetchImpl(endpoint, { credentials: "include" });
    if (!response.ok) return { token: null, cookieName: null };
    const body = (await response.json()) as { token?: unknown; cookieName?: unknown };
    return {
      token: typeof body.token === "string" ? body.token : null,
      cookieName: typeof body.cookieName === "string" ? body.cookieName : null,
    };
  }

  return {
    async token() {
      // Read the cookie for every request: login can rotate it in another
      // GraphQL client or another browser tab.
      const current = readCsrfCookie(cookieName);
      if (current) return current;
      // HttpOnly/session-backed CSRF has no readable cookie; fetch a fresh
      // token, sharing only concurrent reads rather than caching across logins.
      let bootstrap = bootstraps.get(endpoint);
      if (!bootstrap) {
        const pending = load().finally(() => {
          if (bootstraps.get(endpoint) === pending) bootstraps.delete(endpoint);
        });
        bootstraps.set(endpoint, pending);
        bootstrap = pending;
      }
      const loaded = await bootstrap;
      cookieName = loaded.cookieName;
      // The cookie holds the secret that survived; prefer it to the body's token.
      return readCsrfCookie(cookieName) ?? loaded.token;
    },
    clear() {
      cookieName = null;
    },
  };
}

export type AuthFetch = (baseFetch: FetchFn) => FetchFn;

/** HTTP preview actor. Read at dispatch time; never attach it to a WebSocket. */
export function viewAsAuth(getUserId: () => string | null): AuthFetch {
  return (baseFetch) => (input, init) => {
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    const userId = getUserId();
    if (userId !== null) headers.set("X-Angee-View-As", userId);
    else headers.delete("X-Angee-View-As");
    return baseFetch(input, { ...init, headers });
  };
}

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
